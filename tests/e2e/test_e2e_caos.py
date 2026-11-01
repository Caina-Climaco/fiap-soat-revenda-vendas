"""Demonstracao de falha (ADR-004 da solucao): o principal cai no meio das compras.

O teste escala o Deployment do principal para 0, confere que a vitrine continua no ar
(replica de leitura), que a compra e desfeita na hora com 503 e o veiculo volta a vitrine,
e que, com o principal de volta, o circuit breaker fecha e a compra segue. Sempre restaura
o principal, mesmo se falhar no meio.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from apoio_e2e import anuncio, comprar, esperar, tipo

pytestmark = [pytest.mark.e2e, pytest.mark.caos]

NS = os.environ.get("E2E_NS_PRINCIPAL", "principal")
DEPLOYMENT = "deployment/revenda-principal"


def _kubectl(*args: str) -> str:
    # kubectl do PATH do runner do CD, com argumentos fixos deste modulo
    comando = ["kubectl", "-n", NS, *args]
    return subprocess.run(  # noqa: S603
        comando, check=True, capture_output=True, text=True, timeout=200
    ).stdout


class Principal:
    """Derruba e restaura o Deployment do principal (restaurar e idempotente)."""

    def __init__(self) -> None:
        self.fora = False

    def derrubar(self) -> None:
        _kubectl("scale", DEPLOYMENT, "--replicas=0")
        self.fora = True
        _kubectl("wait", "--for=delete", "pod", "-l", "app=revenda-principal", "--timeout=120s")

    def restaurar(self) -> None:
        if not self.fora:
            return
        _kubectl("scale", DEPLOYMENT, "--replicas=2")
        _kubectl("rollout", "status", DEPLOYMENT, "--timeout=180s")
        self.fora = False


@pytest.fixture
def principal() -> Iterator[Principal]:
    controle = Principal()
    try:
        yield controle
    finally:
        controle.restaurar()


def test_principal_fora_compra_desfeita_e_recuperacao(
    http: httpx.Client, novo_veiculo: Callable[..., dict[str, Any]], principal: Principal
) -> None:
    veiculo = novo_veiculo()
    publicado = esperar(lambda: anuncio(http, veiculo["id"]), "anuncio na vitrine")

    principal.derrubar()

    # A vitrine nao depende do principal (replica de leitura)
    assert http.get("/api/v1/veiculos/a-venda").status_code == 200

    falha = comprar(http, veiculo["id"], publicado["versao"])
    assert falha.status_code == 503, falha.text
    assert tipo(falha) == "servico-principal-indisponivel"
    assert int(falha.headers["retry-after"]) > 0
    # C1 na hora: o veiculo voltou a vitrine
    assert anuncio(http, veiculo["id"])["status"] == "A_VENDA"

    # Mais falhas abrem o circuito: continua 503, agora sem esperar o principal
    for _ in range(5):
        assert comprar(http, veiculo["id"], publicado["versao"]).status_code == 503

    principal.restaurar()

    # Com o principal de volta, o circuito passa a meio-aberto e fecha na primeira compra boa
    resposta = esperar(
        lambda: (r := comprar(http, veiculo["id"], publicado["versao"])).status_code == 201 and r,
        "compra aceita com o principal de volta",
        90,
    )
    assert resposta.json()["status"] == "AGUARDANDO_PAGAMENTO"
