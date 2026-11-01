"""Fixtures dos testes ponta a ponta (marcador ``e2e``), sempre pelo API Gateway (Kong).

Dependencias: apenas ``pytest`` e ``httpx`` (``tests/e2e/requirements.txt``); este modulo
nao importa o pacote ``vendas``. O servico de venda depende do principal implantado
(o gestor cadastra os veiculos por ele, e o pagamento chega pelo webhook dele).

=============================  ===========  ==============================================
Variavel                       Obrigatoria  Uso
=============================  ===========  ==============================================
E2E_GATEWAY_URL                nao          Kong (padrao http://localhost:8080)
E2E_JAEGER_URL                 nao          Jaeger (padrao http://localhost:16686)
E2E_GESTOR_APIKEY              sim          Chave do consumer ``gestor``
E2E_GATEWAY_PAGAMENTO_APIKEY   sim          Chave do consumer ``gateway-pagamento``
E2E_WEBHOOK_SECRET             sim          Valor do header ``X-Webhook-Secret``
E2E_CAOS                       nao          ``1``: roda o teste que derruba o principal
E2E_NS_PRINCIPAL               nao          Namespace do principal (padrao ``principal``)
E2E_EXIGIR                     nao          ``1`` transforma variavel ausente em erro (CD)
=============================  ===========  ==============================================
"""

from __future__ import annotations

import os
import random
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from apoio_e2e import Ambiente

OBRIGATORIAS = ("E2E_GESTOR_APIKEY", "E2E_GATEWAY_PAGAMENTO_APIKEY", "E2E_WEBHOOK_SECRET")
TIMEOUT = httpx.Timeout(20.0, connect=5.0)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    exigir = os.environ.get("E2E_EXIGIR") == "1"
    faltando = [v for v in OBRIGATORIAS if not os.environ.get(v)]
    if faltando and exigir:
        raise pytest.UsageError(f"Variaveis obrigatorias ausentes: {', '.join(faltando)}")
    sem_caos = os.environ.get("E2E_CAOS") != "1"
    for item in items:
        if faltando:
            item.add_marker(pytest.mark.skip(reason=f"defina {', '.join(faltando)}"))
        elif sem_caos and item.get_closest_marker("caos"):
            item.add_marker(pytest.mark.skip(reason="teste de falha desligado (E2E_CAOS)"))


@pytest.fixture(scope="session")
def ambiente() -> Ambiente:
    return Ambiente(
        gateway=os.environ.get("E2E_GATEWAY_URL", "http://localhost:8080").rstrip("/"),
        jaeger=os.environ.get("E2E_JAEGER_URL", "http://localhost:16686").rstrip("/"),
        chave_gestor=os.environ.get("E2E_GESTOR_APIKEY", ""),
        chave_pagamento=os.environ.get("E2E_GATEWAY_PAGAMENTO_APIKEY", ""),
        segredo_webhook=os.environ.get("E2E_WEBHOOK_SECRET", ""),
        vendas=True,
    )


@pytest.fixture(scope="session")
def http(ambiente: Ambiente) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=ambiente.gateway, timeout=TIMEOUT) as cliente:
        yield cliente


@pytest.fixture(scope="session")
def gestor(ambiente: Ambiente, http: httpx.Client) -> dict[str, str]:
    return {"apikey": ambiente.chave_gestor}


@pytest.fixture
def novo_veiculo(http: httpx.Client, gestor: dict[str, str]) -> Callable[..., dict[str, Any]]:
    """Cadastra um veiculo unico pelo Kong (preco aleatorio para nao colidir entre execucoes)."""

    def criar(**campos: Any) -> dict[str, Any]:
        corpo = {
            "marca": "E2E",
            "modelo": f"Modelo {uuid.uuid4().hex[:8]}",
            "ano": 2022,
            "cor": "Prata",
            "preco": f"{random.randint(30_000, 90_000)}.00",
            **campos,
        }
        resposta = http.post("/api/v1/veiculos", json=corpo, headers=gestor)
        assert resposta.status_code == 201, resposta.text
        return resposta.json()

    return criar
