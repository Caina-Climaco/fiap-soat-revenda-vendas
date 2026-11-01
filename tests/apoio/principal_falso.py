"""Serviço principal falso: as rotas internas da saga, com o mesmo contrato (docs/05, 5.6).

Usado como transporte do httpx (`httpx.MockTransport(principal)`), reproduz as regras que
importam para a saga: idempotência pela `compra_id`, versão do veículo, regra contra
ressurreição e a cobrança como árbitro do pivô. Permite simular indisponibilidade.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

PROBLEMA = "application/problem+json"


@dataclass
class VeiculoFalso:
    versao: int = 1
    status: str = "A_VENDA"
    compra_id: str | None = None


@dataclass
class CobrancaFalsa:
    codigo: str
    status: str = "PENDENTE"


@dataclass
class PrincipalFalso:
    veiculos: dict[str, VeiculoFalso] = field(default_factory=dict)
    cobrancas: dict[str, CobrancaFalsa] = field(default_factory=dict)
    compensadas: set[str] = field(default_factory=set)
    chamadas: list[tuple[str, str]] = field(default_factory=list)
    fora_do_ar: bool = False
    # Respostas forçadas por trecho do caminho (consumidas na ordem): um status (corpo em
    # texto) ou (status, tipo do problema). Ex.: {"bloqueio": [503, (409, "compra-compensada")]}
    falhas: dict[str, list[int | tuple[int, str]]] = field(default_factory=dict)
    # Executado a cada chamada (ex.: avançar o relógio para simular chamadas lentas)
    ao_chamar: Callable[[], None] | None = None

    def __call__(self, requisicao: httpx.Request) -> httpx.Response:
        caminho = requisicao.url.path
        self.chamadas.append((requisicao.method, caminho))
        if self.ao_chamar is not None:
            self.ao_chamar()
        if self.fora_do_ar:
            raise httpx.ConnectError("principal fora do ar", request=requisicao)
        for trecho, codigos in self.falhas.items():
            if trecho in caminho and codigos:
                falha = codigos.pop(0)
                if isinstance(falha, tuple):
                    return _problema(*falha)
                return httpx.Response(falha, text="falha simulada")
        corpo: dict[str, Any] = json.loads(requisicao.content) if requisicao.content else {}
        partes = caminho.removeprefix("/internal/v1/").split("/")
        if partes[0] == "veiculos":
            return self._veiculos(requisicao.method, partes, corpo)
        return self._cobrancas(partes, corpo)

    # ------------------------------------------------------------ atalhos dos testes

    def veiculo(self, veiculo_id: str) -> VeiculoFalso:
        return self.veiculos.setdefault(veiculo_id, VeiculoFalso())

    def pagar(self, compra_id: str) -> str:
        """O processador confirma o pagamento (webhook no principal real)."""
        cobranca = self.cobrancas[compra_id]
        cobranca.status = "PAGA"
        return cobranca.codigo

    def caminhos(self, trecho: str) -> list[str]:
        return [c for _, c in self.chamadas if trecho in c]

    # ------------------------------------------------------------ catálogo

    def _veiculos(self, metodo: str, partes: list[str], corpo: dict[str, Any]) -> httpx.Response:
        veiculo = self.veiculo(partes[1])
        if partes[2] == "bloqueio" and metodo == "POST":
            compra = corpo["compra_id"]
            if compra in self.compensadas:
                return _problema(409, "compra-compensada")
            if veiculo.compra_id == compra:
                return _ok(veiculo)
            if veiculo.status != "A_VENDA":
                return _problema(409, "veiculo-indisponivel")
            if corpo["versao"] != veiculo.versao:
                return _problema(409, "versao-divergente")
            veiculo.status, veiculo.compra_id = "BLOQUEADO", compra
            return _ok(veiculo)
        if partes[2] == "bloqueio":  # DELETE .../bloqueio/{compra_id}
            compra = partes[3]
            self.compensadas.add(compra)
            if veiculo.compra_id == compra:
                if veiculo.status == "VENDIDO":
                    return _problema(409, "veiculo-ja-vendido")
                veiculo.status, veiculo.compra_id = "A_VENDA", None
            return _ok(veiculo)
        # POST .../venda
        if veiculo.compra_id != corpo["compra_id"]:
            return _problema(409, "bloqueio-de-outra-compra")
        veiculo.status = "VENDIDO"
        return _ok(veiculo)

    # ------------------------------------------------------------ pagamentos

    def _cobrancas(self, partes: list[str], corpo: dict[str, Any]) -> httpx.Response:
        if len(partes) == 1:  # POST /cobrancas
            compra = corpo["compra_id"]
            existente = self.cobrancas.get(compra)
            if existente is not None:
                if existente.status == "CANCELADA" and compra in self.compensadas:
                    return _problema(409, "compra-compensada")
                return httpx.Response(200, json=_cobranca(compra, existente))
            nova = CobrancaFalsa(codigo=f"PAG-{secrets.token_hex(6)}")
            self.cobrancas[compra] = nova
            return httpx.Response(201, json=_cobranca(compra, nova))
        compra = partes[1]  # POST /cobrancas/{compra_id}/cancelamento
        cobranca = self.cobrancas.setdefault(
            compra, CobrancaFalsa(codigo=f"PAG-{secrets.token_hex(6)}", status="CANCELADA")
        )
        if cobranca.status == "PAGA":
            return _problema(409, "cobranca-ja-paga")
        self.compensadas.add(compra)
        cobranca.status = "CANCELADA"
        return httpx.Response(200, json=_cobranca(compra, cobranca))


def _ok(veiculo: VeiculoFalso) -> httpx.Response:
    return httpx.Response(
        200,
        json={"status": veiculo.status, "versao": veiculo.versao, "compra_id": veiculo.compra_id},
    )


def _cobranca(compra: str, cobranca: CobrancaFalsa) -> dict[str, Any]:
    return {"codigo_pagamento": cobranca.codigo, "compra_id": compra, "status": cobranca.status}


def _problema(status: int, tipo: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": f"urn:revenda:problema:{tipo}", "title": tipo, "status": status},
        headers={"content-type": PROBLEMA},
    )
