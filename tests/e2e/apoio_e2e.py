"""Apoio dos testes ponta a ponta: tipos e funcoes usados pelos modulos de teste."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

PROBLEMA = "urn:revenda:problema:"
# CPF valido de teste (digitos verificadores corretos), o exemplo classico de documentacao
CPF_DE_TESTE = "52998224725"


@dataclass(frozen=True)
class Ambiente:
    gateway: str
    jaeger: str
    chave_gestor: str
    chave_pagamento: str
    segredo_webhook: str
    vendas: bool


def tipo(resposta: httpx.Response) -> str:
    """Nome do problema RFC 9457 (sem o prefixo urn:revenda:problema:)."""
    corpo = resposta.json()
    return str(corpo.get("type", "")).removeprefix(PROBLEMA)


def esperar(condicao: Callable[[], Any], descricao: str, limite: float = 30.0) -> Any:
    """Repete `condicao` ate devolver algo verdadeiro (replicacao e relay sao assincronos)."""
    fim = time.monotonic() + limite
    while True:
        valor = condicao()
        if valor:
            return valor
        if time.monotonic() > fim:
            pytest.fail(f"Tempo esgotado esperando: {descricao}")
        time.sleep(0.5)


# ---------------------------------------------------------------- fluxo da compra


def anuncio(http: httpx.Client, veiculo_id: str, versao: int = 1) -> dict[str, Any] | None:
    """O anuncio na vitrine, quando a replica ja chegou com pelo menos `versao`."""
    resposta = http.get(f"/api/v1/anuncios/{veiculo_id}")
    if resposta.status_code != 200:
        return None
    corpo: dict[str, Any] = resposta.json()
    return corpo if corpo["versao"] >= versao else None


def comprar(
    http: httpx.Client, veiculo_id: str, versao: int, cpf: str = CPF_DE_TESTE
) -> httpx.Response:
    return http.post(
        "/api/v1/compras", json={"veiculo_id": veiculo_id, "versao": versao, "cpf": cpf}
    )


def compra(http: httpx.Client, compra_id: str) -> dict[str, Any]:
    corpo: dict[str, Any] = http.get(f"/api/v1/compras/{compra_id}").json()
    return corpo


def notificar(http: httpx.Client, ambiente: Ambiente, codigo: str, status: str) -> httpx.Response:
    """O processador de pagamentos chamando o webhook do principal pelo Kong."""
    return http.post(
        "/api/v1/pagamentos/webhook",
        json={"codigo_pagamento": codigo, "status": status},
        headers={"apikey": ambiente.chave_pagamento, "X-Webhook-Secret": ambiente.segredo_webhook},
    )
