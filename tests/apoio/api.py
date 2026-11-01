"""Atalhos HTTP para os testes de API (TestClient) do serviço de venda."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from apoio.cenario import ANUNCIO, CPF, TOKEN_ENTRADA

PROBLEM_JSON = "application/problem+json"
SERVICO = {"X-Service-Token": TOKEN_ENTRADA}


class Api:
    def __init__(self, cliente: TestClient) -> None:
        self.http = cliente

    # ------------------------------------------------------------ réplica (outbox do principal)
    def replicar(self, veiculo_id: str | None = None, **campos: Any) -> str:
        veiculo_id = veiculo_id or str(uuid.uuid4())
        resposta = self.http.put(
            f"/internal/v1/anuncios/{veiculo_id}", json={**ANUNCIO, **campos}, headers=SERVICO
        )
        assert resposta.status_code == 200, resposta.text
        return veiculo_id

    # ------------------------------------------------------------ vitrine
    def a_venda(self, **params: int) -> dict[str, Any]:
        resposta = self.http.get("/api/v1/veiculos/a-venda", params=params)
        assert resposta.status_code == 200, resposta.text
        corpo: dict[str, Any] = resposta.json()
        return corpo

    def vendidos(self, **params: int) -> dict[str, Any]:
        resposta = self.http.get("/api/v1/veiculos/vendidos", params=params)
        assert resposta.status_code == 200, resposta.text
        corpo: dict[str, Any] = resposta.json()
        return corpo

    def anuncio(self, veiculo_id: str) -> dict[str, Any]:
        resposta = self.http.get(f"/api/v1/anuncios/{veiculo_id}")
        assert resposta.status_code == 200, resposta.text
        corpo: dict[str, Any] = resposta.json()
        return corpo

    # ------------------------------------------------------------ compra
    def comprar(self, veiculo_id: str, versao: int = 1, cpf: str = CPF) -> Response:
        return self.http.post(
            "/api/v1/compras", json={"veiculo_id": veiculo_id, "versao": versao, "cpf": cpf}
        )

    def compra_ok(self, veiculo_id: str, versao: int = 1) -> dict[str, Any]:
        resposta = self.comprar(veiculo_id, versao)
        assert resposta.status_code == 201, resposta.text
        corpo: dict[str, Any] = resposta.json()
        return corpo

    def compra(self, compra_id: str) -> dict[str, Any]:
        resposta = self.http.get(f"/api/v1/compras/{compra_id}")
        assert resposta.status_code == 200, resposta.text
        corpo: dict[str, Any] = resposta.json()
        return corpo

    def resultado(self, compra_id: str, codigo: str, status: str) -> Response:
        return self.http.post(
            f"/internal/v1/compras/{compra_id}/resultado-pagamento",
            json={"codigo_pagamento": codigo, "status": status},
            headers=SERVICO,
        )


def tipo_problema(resposta: Response) -> str:
    assert resposta.headers["content-type"].startswith(PROBLEM_JSON), resposta.text
    tipo: str = resposta.json()["type"]
    return tipo.removeprefix("urn:revenda:problema:")
