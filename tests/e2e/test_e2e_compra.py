"""O fluxo da compra de ponta a ponta, pelo Kong, nos dois servicos implantados.

Mesmos cenarios do Gherkin (tests/bdd/features/compra.feature) que rodam no CI contra o
principal simulado; aqui contra o principal real. A reserva expirada fica so no CI (o prazo
e de 30 minutos).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from apoio_e2e import Ambiente, anuncio, compra, comprar, esperar, notificar, tipo

pytestmark = pytest.mark.e2e


@pytest.fixture
def anunciado(http: httpx.Client, novo_veiculo: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    veiculo = novo_veiculo()
    publicado: dict[str, Any] = esperar(lambda: anuncio(http, veiculo["id"]), "anuncio na vitrine")
    return publicado


def test_compra_paga_vira_venda(
    http: httpx.Client, ambiente: Ambiente, gestor: dict[str, str], anunciado: dict[str, Any]
) -> None:
    veiculo_id = anunciado["veiculo_id"]
    resposta = comprar(http, veiculo_id, anunciado["versao"])
    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert resposta.headers["location"].endswith(corpo["id"])
    assert corpo["status"] == "AGUARDANDO_PAGAMENTO"
    assert corpo["cpf"] == "***.982.247-**"
    assert corpo["codigo_pagamento"].startswith("PAG-")
    # Fora da vitrine enquanto aguarda o pagamento; o gestor nao edita
    assert esperar(lambda: anuncio(http, veiculo_id)["status"] == "RESERVADO", "reservado")
    edicao = http.patch(f"/api/v1/veiculos/{veiculo_id}", json={"cor": "Rosa"}, headers=gestor)
    assert edicao.status_code == 409

    assert notificar(http, ambiente, corpo["codigo_pagamento"], "PAGO").status_code == 200

    efetivada = esperar(
        lambda: (c := compra(http, corpo["id"]))["status"] == "EFETIVADA" and c, "efetivada"
    )
    assert efetivada["data_venda"]
    assert efetivada["cpf"] == "***.982.247-**"
    vendidos = http.get("/api/v1/veiculos/vendidos", params={"limite": 100}).json()["itens"]
    assert veiculo_id in {v["veiculo_id"] for v in vendidos}
    esperar(
        lambda: (
            http.get(f"/api/v1/veiculos/{veiculo_id}", headers=gestor).json()["status"] == "VENDIDO"
        ),
        "vendido no catalogo do principal",
    )


def test_pagamento_cancelado_devolve_a_vitrine(
    http: httpx.Client, ambiente: Ambiente, anunciado: dict[str, Any]
) -> None:
    corpo = comprar(http, anunciado["veiculo_id"], anunciado["versao"]).json()
    assert notificar(http, ambiente, corpo["codigo_pagamento"], "CANCELADO").status_code == 200
    cancelada = esperar(
        lambda: (c := compra(http, corpo["id"]))["status"] == "CANCELADA" and c, "cancelada"
    )
    assert cancelada["motivo_cancelamento"] == "PAGAMENTO_CANCELADO"
    esperar(
        lambda: anuncio(http, anunciado["veiculo_id"])["status"] == "A_VENDA",
        "anuncio de volta a vitrine",
    )
    # E pode ser comprado de novo
    assert comprar(http, anunciado["veiculo_id"], anunciado["versao"]).status_code == 201


def test_segunda_compra_do_mesmo_veiculo_e_recusada(
    http: httpx.Client, anunciado: dict[str, Any]
) -> None:
    assert comprar(http, anunciado["veiculo_id"], anunciado["versao"]).status_code == 201
    segunda = comprar(http, anunciado["veiculo_id"], anunciado["versao"], cpf="11144477735")
    assert segunda.status_code == 409
    assert tipo(segunda) == "veiculo-indisponivel"


def test_cpf_invalido_e_recusado(http: httpx.Client, anunciado: dict[str, Any]) -> None:
    resposta = comprar(http, anunciado["veiculo_id"], anunciado["versao"], cpf="12345678900")
    assert resposta.status_code == 422
    assert tipo(resposta) == "cpf-invalido"
    assert "12345678900" not in resposta.text


def test_veiculo_inexistente(http: httpx.Client) -> None:
    resposta = comprar(http, "00000000-0000-4000-8000-000000000000", 1)
    assert resposta.status_code == 404
    assert tipo(resposta) == "anuncio-nao-encontrado"
