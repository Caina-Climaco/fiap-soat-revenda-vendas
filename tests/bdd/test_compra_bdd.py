"""Passos dos cenários de `features/compra.feature` (pytest-bdd)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import Response
from pytest_bdd import given, parsers, scenarios, then, when

from apoio.api import Api, tipo_problema
from apoio.principal_falso import PrincipalFalso
from vendas.composicao import Componentes
from vendas.shared.clock import RelogioFixo

scenarios("features/compra.feature")


@pytest.fixture
def contexto() -> dict[str, Any]:
    return {}


@given(parsers.parse('um veículo à venda por "{preco}" na vitrine'))
def veiculo_a_venda(api: Api, contexto: dict[str, Any], preco: str) -> None:
    contexto["veiculo"] = api.replicar(preco=preco)


@given("que o serviço principal está fora do ar")
def principal_fora(principal: PrincipalFalso) -> None:
    principal.fora_do_ar = True


@when(parsers.parse('o comprador pede a compra com o CPF "{cpf}"'))
def pedir_compra(api: Api, contexto: dict[str, Any], cpf: str) -> None:
    resposta: Response = api.comprar(contexto["veiculo"], cpf=cpf)
    contexto["resposta"] = resposta
    if resposta.status_code == 201:
        contexto["compra"] = resposta.json()


@when("o processador informa que o pagamento foi efetuado")
def pagamento_efetuado(api: Api, principal: PrincipalFalso, contexto: dict[str, Any]) -> None:
    compra_id = contexto["compra"]["id"]
    contexto["compra"] = api.resultado(compra_id, principal.pagar(compra_id), "PAGO").json()


@when("o processador informa que o pagamento foi cancelado")
def pagamento_cancelado(api: Api, principal: PrincipalFalso, contexto: dict[str, Any]) -> None:
    compra_id = contexto["compra"]["id"]
    cobranca = principal.cobrancas[compra_id]
    cobranca.status = "CANCELADA"
    contexto["compra"] = api.resultado(compra_id, cobranca.codigo, "CANCELADO").json()


@when(parsers.parse("passam {minutos:d} minutos sem resultado do pagamento"))
def passam_minutos(relogio: RelogioFixo, minutos: int) -> None:
    relogio.avancar(timedelta(minutes=minutos))


@when("a recuperação de sagas é executada")
def recuperar(api: Api, componentes: Componentes, contexto: dict[str, Any]) -> None:
    componentes.recuperar.executar()
    contexto["compra"] = api.compra(contexto["compra"]["id"])


@then("a compra fica aguardando pagamento com um código de pagamento")
def aguardando(contexto: dict[str, Any]) -> None:
    assert contexto["compra"]["status"] == "AGUARDANDO_PAGAMENTO"
    assert contexto["compra"]["codigo_pagamento"].startswith("PAG-")


@then("o veículo sai da vitrine e fica bloqueado no catálogo")
def fora_da_vitrine(api: Api, principal: PrincipalFalso, contexto: dict[str, Any]) -> None:
    assert api.a_venda()["total"] == 0
    assert principal.veiculo(contexto["veiculo"]).status == "BLOQUEADO"


@then("a compra é efetivada com a data da venda")
def efetivada(contexto: dict[str, Any]) -> None:
    assert contexto["compra"]["status"] == "EFETIVADA"
    assert contexto["compra"]["data_venda"] is not None


@then(parsers.parse('o veículo aparece entre os vendidos por "{preco}"'))
def nos_vendidos(api: Api, contexto: dict[str, Any], preco: str) -> None:
    itens = api.vendidos()["itens"]
    assert [(i["veiculo_id"], i["preco_venda"]) for i in itens] == [(contexto["veiculo"], preco)]


@then("o catálogo registra o veículo como vendido")
def vendido_no_catalogo(principal: PrincipalFalso, contexto: dict[str, Any]) -> None:
    assert principal.veiculo(contexto["veiculo"]).status == "VENDIDO"


@then(parsers.parse('a compra é cancelada com o motivo "{motivo}"'))
def cancelada(contexto: dict[str, Any], motivo: str) -> None:
    assert contexto["compra"]["status"] == "CANCELADA"
    assert contexto["compra"]["motivo_cancelamento"] == motivo


@then("a cobrança é cancelada no principal")
def cobranca_cancelada(principal: PrincipalFalso, contexto: dict[str, Any]) -> None:
    assert principal.cobrancas[contexto["compra"]["id"]].status == "CANCELADA"


@then("o veículo volta à vitrine e é desbloqueado no catálogo")
def de_volta(api: Api, principal: PrincipalFalso, contexto: dict[str, Any]) -> None:
    assert api.anuncio(contexto["veiculo"])["status"] == "A_VENDA"
    assert principal.veiculo(contexto["veiculo"]).status == "A_VENDA"


@then(parsers.parse('a resposta é {status:d} com o problema "{tipo}"'))
def resposta_problema(contexto: dict[str, Any], status: int, tipo: str) -> None:
    assert contexto["resposta"].status_code == status
    assert tipo_problema(contexto["resposta"]) == tipo


@then("o veículo continua na vitrine")
def continua(api: Api, contexto: dict[str, Any]) -> None:
    assert api.anuncio(contexto["veiculo"])["status"] == "A_VENDA"
