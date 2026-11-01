"""Caminhos de borda da saga: conflito de revisão, recusas e anomalias depois do pivô."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from apoio.api import Api
from apoio.principal_falso import PrincipalFalso
from vendas.composicao import Componentes
from vendas.domain.compra import Compra
from vendas.domain.repositorio import ConflitoRevisaoError
from vendas.shared.clock import RelogioFixo
from vendas.shared.mongo import BancoMongo


def test_conflito_de_revisao_no_meio_da_saga_rele_e_continua(
    api: Api, componentes: Componentes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O repasse do pagamento grava no documento enquanto a saga anda: ela relê e segue."""
    repo = componentes.orquestrador._compras
    original = repo.salvar
    conflitos = iter([True])

    def salvar_com_um_conflito(compra: Compra) -> None:
        if next(conflitos, False):
            raise ConflitoRevisaoError(str(compra.id))
        original(compra)

    monkeypatch.setattr(repo, "salvar", salvar_com_um_conflito)
    compra = api.compra_ok(api.replicar())
    assert compra["status"] == "AGUARDANDO_PAGAMENTO"


def test_compra_compensada_no_catalogo_vira_veiculo_indisponivel(
    api: Api, principal: PrincipalFalso
) -> None:
    principal.falhas["bloqueio"] = [(409, "compra-compensada")]
    resposta = api.comprar(api.replicar())
    assert resposta.status_code == 409


def test_recusa_inesperada_em_t3_cancela_por_falha(api: Api, principal: PrincipalFalso) -> None:
    principal.falhas["cobrancas"] = [(409, "compra-compensada")]
    resposta = api.comprar(api.replicar())
    assert resposta.status_code == 503


def test_recusa_inesperada_no_c3_nao_impede_o_cancelamento(
    api: Api, principal: PrincipalFalso, componentes: Componentes, relogio: RelogioFixo
) -> None:
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    principal.falhas["cancelamento"] = [(409, "cobranca-nao-encontrada")]
    relogio.avancar(timedelta(minutes=31))
    componentes.recuperar.executar()
    assert api.compra(compra["id"])["motivo_cancelamento"] == "RESERVA_EXPIRADA"
    assert api.anuncio(veiculo)["status"] == "A_VENDA"


def test_c3_fora_do_ar_na_expiracao_adia_sem_cancelar(
    api: Api, principal: PrincipalFalso, componentes: Componentes, relogio: RelogioFixo
) -> None:
    """Na espera do pagamento, o C3 decide: sem resposta dele, a compra não é cancelada."""
    compra = api.compra_ok(api.replicar())
    principal.fora_do_ar = True
    relogio.avancar(timedelta(minutes=31))
    componentes.recuperar.executar()
    assert api.compra(compra["id"])["status"] == "AGUARDANDO_PAGAMENTO"
    principal.fora_do_ar = False
    relogio.avancar(timedelta(minutes=1))
    componentes.recuperar.executar()
    assert api.compra(compra["id"])["status"] == "CANCELADA"


def test_desbloqueio_recusado_fica_registrado_e_a_compensacao_termina(
    api: Api, principal: PrincipalFalso, componentes: Componentes, relogio: RelogioFixo
) -> None:
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    principal.veiculo(veiculo).status = "VENDIDO"  # anomalia: o catálogo já vendeu
    relogio.avancar(timedelta(minutes=31))
    componentes.recuperar.executar()
    texto = api.http.get("/metrics").text
    assert 'passo="C2_DESBLOQUEIO",resultado="RECUSADO:veiculo-ja-vendido"' in texto
    assert componentes.recuperar.executar().sagas_processadas == 0  # nada mais pendente
    assert api.compra(compra["id"])["status"] == "CANCELADA"


def test_anomalias_depois_do_pivo_sao_registradas_sem_travar(
    api: Api, banco: BancoMongo, principal: PrincipalFalso
) -> None:
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    # O anúncio perdeu a reserva e o catálogo está bloqueado por outra compra.
    banco.db.anuncios.update_one({"_id": veiculo}, {"$set": {"compra_id": str(uuid.uuid4())}})
    principal.veiculo(veiculo).compra_id = str(uuid.uuid4())
    efetivada = api.resultado(compra["id"], principal.pagar(compra["id"]), "PAGO").json()
    assert efetivada["status"] == "EFETIVADA"
    texto = api.http.get("/metrics").text
    assert 'resultado="ANOMALIA_ANUNCIO"' in texto
    assert 'resultado="RECUSADO:bloqueio-de-outra-compra"' in texto
