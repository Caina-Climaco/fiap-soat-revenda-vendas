"""Adaptadores HTTP das portas da saga para as rotas internas do principal (docs/05, 5.6)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import httpx

from vendas.application.portas import ParticipanteIndisponivelError, RecusaDoParticipanteError
from vendas.infrastructure.resiliencia import ClienteResiliente

PREFIXO_PROBLEMA = "urn:revenda:problema:"


def _verificar(cliente: ClienteResiliente, resposta: httpx.Response) -> httpx.Response:
    if resposta.status_code < 300:
        return resposta
    tipo = ""
    if resposta.headers.get("content-type", "").startswith("application/problem+json"):
        tipo = str(resposta.json().get("type", "")).removeprefix(PREFIXO_PROBLEMA)
    if resposta.status_code in (404, 409) and tipo:
        raise RecusaDoParticipanteError(tipo, resposta.status_code)
    # 401 (token errado), 422 (contrato quebrado) e afins: configuração ou versão
    # incompatível; tratados como indisponibilidade para a saga não decidir em cima disso.
    raise ParticipanteIndisponivelError(
        cliente.destino, f"HTTP {resposta.status_code} {tipo}".strip()
    )


class CatalogoHttp:
    def __init__(self, cliente: ClienteResiliente) -> None:
        self._cliente = cliente

    def bloquear(self, veiculo_id: UUID, compra_id: UUID, versao: int) -> None:
        _verificar(
            self._cliente,
            self._cliente.chamar(
                "POST",
                f"/internal/v1/veiculos/{veiculo_id}/bloqueio",
                {"compra_id": str(compra_id), "versao": versao},
            ),
        )

    def desbloquear(self, veiculo_id: UUID, compra_id: UUID) -> None:
        _verificar(
            self._cliente,
            self._cliente.chamar(
                "DELETE", f"/internal/v1/veiculos/{veiculo_id}/bloqueio/{compra_id}"
            ),
        )

    def vender(self, veiculo_id: UUID, compra_id: UUID) -> None:
        _verificar(
            self._cliente,
            self._cliente.chamar(
                "POST", f"/internal/v1/veiculos/{veiculo_id}/venda", {"compra_id": str(compra_id)}
            ),
        )


class PagamentosHttp:
    def __init__(self, cliente: ClienteResiliente) -> None:
        self._cliente = cliente

    def gerar_cobranca(self, compra_id: UUID, valor: Decimal) -> str:
        resposta = _verificar(
            self._cliente,
            self._cliente.chamar(
                "POST",
                "/internal/v1/cobrancas",
                {"compra_id": str(compra_id), "valor": f"{valor:.2f}"},
            ),
        )
        return str(resposta.json()["codigo_pagamento"])

    def cancelar_cobranca(self, compra_id: UUID) -> None:
        _verificar(
            self._cliente,
            self._cliente.chamar("POST", f"/internal/v1/cobrancas/{compra_id}/cancelamento"),
        )
