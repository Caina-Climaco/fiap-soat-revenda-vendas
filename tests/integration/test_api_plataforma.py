"""Saúde, OpenAPI, erros, correlação e métricas do serviço de venda."""

from __future__ import annotations

import uuid

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import vendas.main
from apoio.api import Api, tipo_problema
from apoio.cenario import CHAVE_CPF, TOKEN_ENTRADA, TOKEN_SAIDA
from apoio.principal_falso import PrincipalFalso
from vendas.main import criar_app
from vendas.shared.config import Settings
from vendas.shared.mongo import BancoMongo


def test_health_live_e_ready(api: Api) -> None:
    assert api.http.get("/health/live").json() == {"status": "ok"}
    assert api.http.get("/health/ready").json() == {
        "status": "ok",
        "verificacoes": {"banco": "ok"},
    }


class ClienteForaDoAr(mongomock.MongoClient):  # type: ignore[misc]
    """MongoDB inacessível: qualquer comando falha."""

    @property
    def admin(self) -> object:
        raise ConnectionError("fora do ar")


def test_health_ready_503_sem_banco(settings: Settings) -> None:
    banco = BancoMongo(ClienteForaDoAr(), "x")
    with TestClient(criar_app(settings, banco=banco)) as cliente:
        assert cliente.get("/health/live").status_code == 200
        resposta = cliente.get("/health/ready")
    assert resposta.status_code == 503
    assert tipo_problema(resposta) == "indisponivel"


def test_indices_que_falham_na_subida_nao_derrubam_a_app(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def falhar(_db: object) -> None:
        raise ConnectionError("fora do ar")

    monkeypatch.setattr(vendas.main, "garantir_indices", falhar)
    banco = BancoMongo(mongomock.MongoClient(tz_aware=True), "x")
    with TestClient(criar_app(settings, banco=banco)) as cliente:
        assert cliente.get("/health/live").status_code == 200


def test_app_do_uvicorn_e_criado_sob_demanda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_SERVICO_ENTRADA", TOKEN_ENTRADA)
    monkeypatch.setenv("TOKEN_SERVICO_SAIDA", TOKEN_SAIDA)
    monkeypatch.setenv("CPF_CHAVE", CHAVE_CPF)
    monkeypatch.setattr(
        vendas.main.BancoMongo,
        "conectar",
        lambda *_a: BancoMongo(mongomock.MongoClient(tz_aware=True), "x"),
    )
    monkeypatch.setattr(vendas.main, "_app", None)
    app = vendas.main.app
    assert isinstance(app, FastAPI)
    assert vendas.main.app is app
    with pytest.raises(AttributeError):
        _ = vendas.main.nao_existe


def test_openapi_documenta_rotas_e_problem_json(api: Api) -> None:
    esquema = api.http.get("/openapi.json").json()
    assert esquema["components"]["securitySchemes"]["token-servico"]["name"] == "X-Service-Token"
    assert "Problema" in esquema["components"]["schemas"]
    assert {
        "/api/v1/veiculos/a-venda",
        "/api/v1/veiculos/vendidos",
        "/api/v1/compras",
        "/api/v1/compras/{compra_id}",
        "/internal/v1/anuncios/{veiculo_id}",
        "/internal/v1/compras/{compra_id}/resultado-pagamento",
    } <= set(esquema["paths"])


def test_documentacao_com_prefixo_do_kong(settings: Settings) -> None:
    com_prefixo = settings.model_copy(update={"docs_prefixo": "/vendas"})
    banco = BancoMongo(mongomock.MongoClient(tz_aware=True), "x")
    with TestClient(criar_app(com_prefixo, banco=banco)) as cliente:
        pagina = cliente.get("/vendas/docs")
        assert pagina.status_code == 200
        assert "/vendas/openapi.json" in pagina.text
        assert cliente.get("/vendas/openapi.json").json()["paths"]
        assert cliente.get("/docs").status_code == 404


@pytest.mark.parametrize("prefixo", ["vendas", "/vendas/", "/Vendas", "/a b"])
def test_prefixo_da_documentacao_invalido(settings: Settings, prefixo: str) -> None:
    with pytest.raises(ValueError, match="docs_prefixo"):
        Settings.model_validate({**settings.model_dump(), "docs_prefixo": prefixo})


def test_rota_inexistente_e_json_malformado(api: Api) -> None:
    assert api.http.get("/api/v1/nao-existe").json()["type"] == "about:blank"
    resposta = api.http.post(
        "/api/v1/compras", content="{x", headers={"Content-Type": "application/json"}
    )
    assert resposta.status_code == 400
    assert tipo_problema(resposta) == "requisicao-malformada"


def test_x_request_id(api: Api) -> None:
    assert api.http.get("/health/live", headers={"X-Request-ID": "r-1"}).headers[
        "x-request-id"
    ] == ("r-1")
    assert uuid.UUID(api.http.get("/health/live").headers["x-request-id"])


def test_metricas_de_negocio_e_saga(api: Api, principal: PrincipalFalso) -> None:
    compra = api.compra_ok(api.replicar())
    api.resultado(compra["id"], principal.pagar(compra["id"]), "PAGO")
    principal.veiculos.clear()
    outro = api.replicar()
    principal.veiculo(outro).versao = 9
    api.comprar(outro)
    texto = api.http.get("/metrics").text
    assert "revenda_compras_iniciadas_total 2.0" in texto
    assert "revenda_compras_efetivadas_total 1.0" in texto
    assert 'revenda_compras_canceladas_total{motivo="ANUNCIO_ALTERADO"} 1.0' in texto
    assert (
        'revenda_saga_passos_total{passo="T2_BLOQUEIO",resultado="RECUSADO:versao-divergente"} 1.0'
        in texto
    )
    assert 'revenda_saga_passos_total{passo="C1_LIBERACAO",resultado="COMPENSADO"} 1.0' in texto
    assert 'revenda_chamadas_participante_total{destino="principal",resultado="sucesso"}' in texto
    assert 'revenda_circuito_estado{destino="principal"} 0.0' in texto


def test_conexao_real_com_mongo_inacessivel_falha_rapido() -> None:
    banco = BancoMongo.conectar("mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=200", "x")
    try:
        assert banco.disponivel() is False
    finally:
        banco.fechar()
