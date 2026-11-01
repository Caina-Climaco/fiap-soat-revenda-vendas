"""Repositórios MongoDB: operações atômicas de um documento (ADR-001 do serviço de venda)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from apoio.api import Api, tipo_problema
from apoio.cenario import AS_10H
from vendas.domain.repositorio import ResultadoReserva
from vendas.infrastructure.repositorio_mongo import MongoAnuncioRepository, centavos, reais
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
