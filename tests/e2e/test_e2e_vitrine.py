"""Vitrine pelo Kong: listagens publicas ordenadas por preco, sem dados do comprador."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
from apoio_e2e import anuncio, esperar

pytestmark = pytest.mark.e2e


def test_veiculo_cadastrado_aparece_na_vitrine(
    http: httpx.Client, novo_veiculo: Callable[..., dict[str, Any]]
) -> None:
    veiculo = novo_veiculo(cor="Verde")
    publicado = esperar(lambda: anuncio(http, veiculo["id"]), "anuncio na vitrine")
    assert publicado["status"] == "A_VENDA"
    assert publicado["preco"] == veiculo["preco"]
    assert publicado["cor"] == "Verde"


def test_a_venda_do_mais_barato_ao_mais_caro(http: httpx.Client) -> None:
    pagina = http.get("/api/v1/veiculos/a-venda", params={"limite": 100})
    assert pagina.status_code == 200
    precos = [Decimal(v["preco"]) for v in pagina.json()["itens"]]
    assert precos == sorted(precos)


def test_vendidos_do_mais_barato_ao_mais_caro_sem_comprador(http: httpx.Client) -> None:
    pagina = http.get("/api/v1/veiculos/vendidos", params={"limite": 100})
    assert pagina.status_code == 200
    itens = pagina.json()["itens"]
    precos = [Decimal(v["preco_venda"]) for v in itens]
    assert precos == sorted(precos)
    assert all("cpf" not in v for v in itens)


def test_vitrine_tem_rate_limiting_no_kong(http: httpx.Client) -> None:
    resposta = http.get("/api/v1/veiculos/a-venda")
    assert int(resposta.headers["ratelimit-limit"]) > 0


def test_rotas_internas_do_servico_de_venda_fechadas(http: httpx.Client) -> None:
    assert (
        http.put("/internal/v1/anuncios/00000000-0000-0000-0000-000000000000", json={}).status_code
        == 404
    )


def test_documentacao_pelo_kong(http: httpx.Client) -> None:
    assert http.get("/vendas/docs").status_code == 200
    assert "/api/v1/compras" in http.get("/vendas/openapi.json").json()["paths"]
