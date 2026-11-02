"""Recuperação de sagas (ADR-006), lease, conflito de revisão e retenção do CPF."""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from pymongo import MongoClient

from apoio.api import Api
from apoio.cenario import CHAVE_CPF, TOKEN_ENTRADA, TOKEN_SAIDA
from apoio.principal_falso import PrincipalFalso
from vendas import recuperacao
from vendas.composicao import Componentes
from vendas.domain.compra import Passo, StatusCompra
from vendas.domain.erros import VeiculoIndisponivelError
from vendas.domain.repositorio import ConflitoRevisaoError
from vendas.infrastructure.repositorio_mongo import MongoCompraRepository
from vendas.shared.clock import RelogioFixo
from vendas.shared.mongo import BancoMongo


def test_saga_parada_no_meio_e_retomada(
    api: Api,
    banco: BancoMongo,
    principal: PrincipalFalso,
    componentes: Componentes,
    relogio: RelogioFixo,
) -> None:
    """V9: o pod caiu depois do T2; a recuperação conclui T3 quando o lease vence."""
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    banco.db.compras.update_one(
        {"_id": compra["id"]},
        {
            "$set": {
                "status": StatusCompra.PROCESSANDO.value,
                "codigo_pagamento": None,
                "saga.concluidos": [Passo.RESERVA.value, Passo.BLOQUEIO.value],
                "saga.lease_ate": relogio.agora() + timedelta(seconds=30),
                "saga.dono_lease": "pod-que-caiu",
            }
        },
    )
    assert componentes.recuperar.executar().sagas_processadas == 0  # lease ainda válido
    relogio.avancar(timedelta(seconds=31))
    assert componentes.recuperar.executar().sagas_processadas == 1
    retomada = api.compra(compra["id"])
    assert retomada["status"] == "AGUARDANDO_PAGAMENTO"
    # T3 repetido devolveu a mesma cobrança (idempotência pela compra_id).
    assert retomada["codigo_pagamento"] == principal.cobrancas[compra["id"]].codigo


def test_lease_ocupado_impede_dois_processos_na_mesma_saga(
    api: Api, banco: BancoMongo, componentes: Componentes, relogio: RelogioFixo
) -> None:
    compra = api.compra_ok(api.replicar())
    repo = MongoCompraRepository(banco.db)
    agora = relogio.agora()
    assert repo.tomar_lease(uuid.UUID(compra["id"]), "outro-pod", agora, timedelta(seconds=30))
    assert componentes.orquestrador.processar(uuid.UUID(compra["id"])) is None
    repo.liberar_lease(uuid.UUID(compra["id"]), "outro-pod")
    assert componentes.orquestrador.processar(uuid.UUID(compra["id"])) is not None


def test_resultado_chega_com_a_saga_ocupada(
    api: Api,
    banco: BancoMongo,
    principal: PrincipalFalso,
    componentes: Componentes,
    relogio: RelogioFixo,
) -> None:
    """O repasse grava o resultado; quem estiver com a saga (ou a recuperação) efetiva."""
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    repo = MongoCompraRepository(banco.db)
    compra_id = uuid.UUID(compra["id"])
    repo.tomar_lease(compra_id, "outro-pod", relogio.agora(), timedelta(minutes=5))
    resposta = api.resultado(compra["id"], principal.pagar(compra["id"]), "PAGO")
    assert resposta.json()["status"] == "AGUARDANDO_PAGAMENTO"
    repo.liberar_lease(compra_id, "outro-pod")
    componentes.recuperar.executar()
    assert api.compra(compra["id"])["status"] == "EFETIVADA"


def test_conflito_de_revisao(api: Api, banco: BancoMongo) -> None:
    compra = api.compra_ok(api.replicar())
    repo = MongoCompraRepository(banco.db)
    a = repo.obter(uuid.UUID(compra["id"]))
    b = repo.obter(uuid.UUID(compra["id"]))
    assert a is not None
    assert b is not None
    repo.salvar(a)
    with pytest.raises(ConflitoRevisaoError):
        repo.salvar(b)


def test_retencao_apaga_cpf_de_compras_canceladas_antigas(
    api: Api, principal: PrincipalFalso, componentes: Componentes, relogio: RelogioFixo
) -> None:
    veiculo = api.replicar()
    principal.veiculo(veiculo).versao = 2
    api.comprar(veiculo)  # cancelada: ANUNCIO_ALTERADO
    efetivada = api.compra_ok(api.replicar())
    api.resultado(efetivada["id"], principal.pagar(efetivada["id"]), "PAGO")
    relogio.avancar(timedelta(days=91))
    assert componentes.recuperar.executar().cpfs_apagados == 1
    assert api.compra(efetivada["id"])["cpf"] is not None  # efetivada mantém (obrigação legal)


def test_main_do_cronjob(
    monkeypatch: pytest.MonkeyPatch, cliente_mongo: MongoClient[dict[str, Any]]
) -> None:
    monkeypatch.setenv("TOKEN_SERVICO_ENTRADA", TOKEN_ENTRADA)
    monkeypatch.setenv("TOKEN_SERVICO_SAIDA", TOKEN_SAIDA)
    monkeypatch.setenv("CPF_CHAVE", CHAVE_CPF)
    banco = BancoMongo(cliente_mongo, "revenda_vendas_cronjob")
    assert recuperacao.main([], banco=banco) == 0
    assert "ux_compra_ativa_por_veiculo" in banco.db.compras.index_information()


@pytest.mark.skipif(
    not os.environ.get("TEST_MONGO_URL"),
    reason="concorrencia de verdade exige o MongoDB real (o CI roda com TEST_MONGO_URL)",
)
def test_compras_simultaneas_do_mesmo_veiculo_so_uma_vence(
    api: Api, componentes: Componentes
) -> None:
    """RNF-02: N compradores ao mesmo tempo; exatamente uma compra fica ativa."""
    veiculo = api.replicar()
    casos_uso = componentes.casos_uso
    largada = threading.Barrier(10)

    def tentar() -> str:
        largada.wait()
        try:
            compra = casos_uso.iniciar_compra.executar(uuid.UUID(veiculo), 1, "52998224725")
        except VeiculoIndisponivelError:
            return "recusada"
        return compra.status.value

    with ThreadPoolExecutor(max_workers=10) as executor:
        resultados = list(executor.map(lambda _: tentar(), range(10)))
    assert resultados.count("AGUARDANDO_PAGAMENTO") == 1
    assert resultados.count("recusada") == 9
    assert api.anuncio(veiculo)["status"] == "RESERVADO"


def test_cronjob_fecha_o_cliente_mongo_que_abriu(
    monkeypatch: pytest.MonkeyPatch, cliente_mongo: MongoClient[dict[str, Any]]
) -> None:
    monkeypatch.setenv("TOKEN_SERVICO_ENTRADA", TOKEN_ENTRADA)
    monkeypatch.setenv("TOKEN_SERVICO_SAIDA", TOKEN_SAIDA)
    monkeypatch.setenv("CPF_CHAVE", CHAVE_CPF)
    fechados: list[bool] = []

    class Espiao(BancoMongo):
        def fechar(self) -> None:
            fechados.append(True)

    monkeypatch.setattr(
        recuperacao.BancoMongo,
        "conectar",
        lambda *_a: Espiao(cliente_mongo, "revenda_vendas_cronjob_proprio"),
    )
    assert recuperacao.main([]) == 0
    assert fechados == [True]


def test_sagas_paradas_contadas_pela_recuperacao_e_expostas_no_metrics(
    api: Api,
    banco: BancoMongo,
    componentes: Componentes,
    relogio: RelogioFixo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-006: saga com trabalho pendente e sem progresso além de SEM_PROGRESSO_ALERTA."""
    em_dia = api.compra_ok(api.replicar())  # aguardando pagamento, dentro do prazo
    parada = api.compra_ok(api.replicar())
    banco.db.compras.update_one(
        {"_id": parada["id"]},
        {
            "$set": {
                "status": StatusCompra.PROCESSANDO.value,
                "atualizada_em": relogio.agora(),
                "saga.lease_ate": relogio.agora() + timedelta(hours=1),  # pod travado
            }
        },
    )
    assert componentes.recuperar.sagas_paradas() == 0  # ainda dentro dos 5 minutos
    relogio.avancar(timedelta(minutes=6))
    assert componentes.recuperar.sagas_paradas() == 1
    assert api.compra(em_dia["id"])["status"] == "AGUARDANDO_PAGAMENTO"
    assert componentes.recuperar.executar().sagas_paradas == 1
    texto = api.http.get("/metrics").text
    assert "revenda_sagas_paradas 1.0" in texto

    def banco_fora() -> int:
        raise RuntimeError("MongoDB fora")

    monkeypatch.setattr(componentes.recuperar, "sagas_paradas", banco_fora)
    resposta = api.http.get("/metrics")
    assert resposta.status_code == 200
    assert "revenda_sagas_paradas NaN" in resposta.text


def test_reserva_que_acabou_de_expirar_nao_conta_como_parada(
    api: Api, componentes: Componentes, relogio: RelogioFixo
) -> None:
    """Revisão do 5.2: a compra que expira agora tem `atualizada_em` de 30 min atrás, mas só
    passou a ter algo a fazer agora. Só conta se a recuperação não agir por 5 minutos."""
    api.compra_ok(api.replicar())  # prazo da reserva: 30 minutos
    relogio.avancar(timedelta(minutes=31))  # expirou há 1 minuto
    assert componentes.recuperar.sagas_paradas() == 0
    relogio.avancar(timedelta(minutes=5))  # expirou há 6 minutos e ninguém agiu
    assert componentes.recuperar.sagas_paradas() == 1
    componentes.recuperar.executar()  # a recuperação expira a compra
    assert componentes.recuperar.sagas_paradas() == 0
