"""Repositórios MongoDB: operações atômicas de um documento (ADR-001 do serviço de venda)."""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure

from apoio.api import Api, tipo_problema
from apoio.cenario import AS_10H
from vendas.composicao import Componentes
from vendas.domain.anuncio import Anuncio, DadosAnuncio
from vendas.domain.repositorio import ResultadoReserva
from vendas.infrastructure.repositorio_mongo import (
    MongoAnuncioRepository,
    MongoCompraRepository,
    _anuncio_para_doc,
    centavos,
    garantir_indices,
    reais,
)
from vendas.shared.mongo import BancoMongo, utc


def test_reserva_liberacao_e_venda_atomicas(api: Api, banco: BancoMongo) -> None:
    repo = MongoAnuncioRepository(banco.db)
    veiculo = uuid.UUID(api.replicar())
    compra, outra = uuid.uuid4(), uuid.uuid4()
    assert repo.reservar(uuid.uuid4(), 1, compra, AS_10H) is ResultadoReserva.NAO_ENCONTRADO
    assert repo.reservar(veiculo, 2, compra, AS_10H) is ResultadoReserva.VERSAO_DIVERGENTE
    assert repo.reservar(veiculo, 1, compra, AS_10H) is ResultadoReserva.RESERVADO
    assert repo.reservar(veiculo, 1, compra, AS_10H) is ResultadoReserva.RESERVADO  # repetição
    assert repo.reservar(veiculo, 1, outra, AS_10H) is ResultadoReserva.INDISPONIVEL
    assert repo.liberar(veiculo, outra, AS_10H) is False
    assert repo.vender(veiculo, outra, Decimal("1.00"), AS_10H) is False
    assert repo.vender(veiculo, compra, Decimal("119900.00"), AS_10H) is True
    assert repo.vender(veiculo, compra, Decimal("119900.00"), AS_10H) is True  # repetição
    assert repo.liberar(veiculo, compra, AS_10H) is False  # vendido não volta
    anuncio = repo.obter(veiculo)
    assert anuncio is not None
    assert (anuncio.preco_venda, anuncio.data_venda) == (Decimal("119900.00"), AS_10H)


def test_corrida_entre_a_leitura_e_a_gravacao_da_compra(api: Api, banco: BancoMongo) -> None:
    """Dois compradores leem o anúncio livre; o índice único decide (RN-03)."""
    veiculo = api.replicar()
    api.compra_ok(veiculo)
    # Simula a leitura antiga do segundo comprador: anúncio ainda à venda.
    banco.db.anuncios.update_one({"_id": veiculo}, {"$set": {"status": "A_VENDA"}})
    resposta = api.comprar(veiculo)
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "veiculo-indisponivel"


def test_conversoes_de_dinheiro_e_datas() -> None:
    assert centavos(Decimal("119900.10")) == 11990010
    assert reais(11990010) == Decimal("119900.10")
    assert utc(None) is None
    assert utc(AS_10H.replace(tzinfo=None)) == AS_10H


def test_lease_nao_e_reentrante_nem_dentro_do_mesmo_processo(api: Api, banco: BancoMongo) -> None:
    """Item 3.2 da revisão: o mesmo dono não toma de novo um lease que está ativo."""
    compra = uuid.UUID(api.compra_ok(api.replicar())["id"])
    repo = MongoCompraRepository(banco.db)
    lease = timedelta(seconds=30)
    assert repo.tomar_lease(compra, "pod-1", AS_10H, lease) is not None
    assert repo.tomar_lease(compra, "pod-1", AS_10H, lease) is None
    assert repo.tomar_lease(compra, "pod-2", AS_10H, lease) is None
    # Vencido, qualquer execução pode retomar.
    assert repo.tomar_lease(compra, "pod-2", AS_10H + timedelta(seconds=31), lease) is not None


def test_cada_execucao_da_saga_tem_um_dono_proprio(componentes: Componentes) -> None:
    orquestrador = componentes.orquestrador
    donos = {orquestrador.novo_dono() for _ in range(50)}
    assert len(donos) == 50
    assert all(dono.startswith(f"{orquestrador.dono}-") for dono in donos)


@pytest.mark.skipif(
    not os.environ.get("TEST_MONGO_URL"),
    reason="concorrencia de verdade exige o MongoDB real (o CI roda com TEST_MONGO_URL)",
)
def test_duas_threads_do_mesmo_processo_disputam_o_lease(api: Api, banco: BancoMongo) -> None:
    compra = uuid.UUID(api.compra_ok(api.replicar())["id"])
    repo = MongoCompraRepository(banco.db)
    largada = threading.Barrier(2)

    def tomar(dono: str) -> bool:
        largada.wait()
        return repo.tomar_lease(compra, dono, AS_10H, timedelta(seconds=30)) is not None

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(tomar, ["pod-1-a", "pod-1-b"]))
    assert sorted(resultados) == [False, True]


def test_replica_mais_nova_em_corrida_com_a_insercao_da_mais_antiga(
    banco: BancoMongo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 3.3 da revisão: v2 perde o insert para v1 e ainda assim é aplicada."""
    repo = MongoAnuncioRepository(banco.db)
    veiculo = uuid.uuid4()

    def dados(versao: int, preco: str) -> DadosAnuncio:
        return DadosAnuncio(
            veiculo_id=veiculo,
            versao=versao,
            marca="Fiat",
            modelo="Argo",
            ano=2022,
            cor="Branco",
            preco=Decimal(preco),
            criado_em=AS_10H,
        )

    colecao = repo._colecao
    inserir_original = colecao.insert_one

    def v1_chega_antes(documento: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        monkeypatch.setattr(colecao, "insert_one", inserir_original)
        inserir_original(_anuncio_para_doc(Anuncio.novo(dados(1, "70000.00"), AS_10H)))
        raise DuplicateKeyError("E11000 duplicate key error")

    monkeypatch.setattr(colecao, "insert_one", v1_chega_antes)
    assert repo.aplicar_replica(dados(2, "65000.00"), AS_10H) is True
    anuncio = repo.obter(veiculo)
    assert anuncio is not None
    assert (anuncio.versao, anuncio.preco) == (2, Decimal("65000.00"))
    # A repetição da v1, já atrasada, continua sem efeito.
    assert repo.aplicar_replica(dados(1, "70000.00"), AS_10H) is False


def test_indices_das_listagens_cobrem_o_sort_e_substituem_os_antigos(
    cliente_mongo: MongoClient[dict[str, Any]],
) -> None:
    """Itens 3.4 e 3.5 da revisão, num banco que ainda tem os índices da versão anterior."""
    cliente_mongo.drop_database("revenda_vendas_indices")
    db = cliente_mongo["revenda_vendas_indices"]
    db.anuncios.create_index(
        [("status", ASCENDING), ("preco_centavos", ASCENDING), ("criado_em", ASCENDING)],
        name="ix_vitrine_a_venda",
    )
    garantir_indices(db)
    garantir_indices(db)  # idempotente
    anuncios = db.anuncios.index_information()
    assert "ix_vitrine_a_venda" not in anuncios
    assert [c for c, _ in anuncios["ix_vitrine_a_venda_ordem"]["key"]] == [
        "status",
        "preco_centavos",
        "criado_em",
        "_id",
    ]
    assert [c for c, _ in anuncios["ix_vitrine_vendidos_ordem"]["key"]][-1] == "_id"
    retencao = db.compras.index_information()["ix_retencao_cpf"]
    assert retencao["partialFilterExpression"] == {"status": "CANCELADA"}


def test_indice_antigo_removido_por_outro_processo_no_meio_nao_aborta(
    cliente_mongo: MongoClient[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revisão do 3.4: várias réplicas e o CronJob sobem juntos; o drop de um índice que
    outro processo acabou de remover não impede a criação dos índices novos."""
    cliente_mongo.drop_database("revenda_vendas_indices_corrida")
    db = cliente_mongo["revenda_vendas_indices_corrida"]
    db.anuncios.create_index([("status", ASCENDING)], name="ix_vitrine_vendidos")

    def ja_removido(_self: object, nome: str) -> None:
        raise OperationFailure(f"index not found with name [{nome}]", code=27)

    monkeypatch.setattr(type(db.anuncios), "drop_index", ja_removido)
    garantir_indices(db)
    assert {"ix_vitrine_a_venda_ordem", "ix_vitrine_vendidos_ordem"} <= set(
        db.anuncios.index_information()
    )
