"""Ambiente dos testes de integração e BDD: MongoDB, a API e o principal falso.

- Com `TEST_MONGO_URL` (o CI usa um MongoDB 7 real), os testes rodam contra o banco de
  verdade; sem ela, contra o MongoDB simulado (mongomock), para rodar em qualquer máquina.
  As duas formas exercitam os mesmos repositórios, índices e consultas.
- Antes de cada teste o banco é apagado e os índices recriados pela própria aplicação.
- O serviço principal é o `PrincipalFalso`, ligado como transporte do httpx: a saga passa
  pelo cliente resiliente real (retry, circuit breaker), sem rede.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import httpx
import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo import MongoClient

from apoio.api import Api
from apoio.cenario import AS_10H, CHAVE_CPF, TOKEN_ENTRADA, TOKEN_SAIDA
from apoio.principal_falso import PrincipalFalso
from vendas.composicao import Componentes
from vendas.main import criar_app
from vendas.shared.clock import RelogioFixo
from vendas.shared.config import Settings
from vendas.shared.mongo import BancoMongo

VARIAVEL_URL = "TEST_MONGO_URL"
NOME_BANCO = "revenda_vendas_test"


def pytest_report_header() -> str:
    url = os.environ.get(VARIAVEL_URL)
    return f"MongoDB: {'real (' + VARIAVEL_URL + ')' if url else 'simulado (mongomock)'}"


@pytest.fixture(scope="session")
def cliente_mongo() -> Iterator[MongoClient[dict[str, Any]]]:
    url = os.environ.get(VARIAVEL_URL)
    if url:
        cliente: MongoClient[dict[str, Any]] = MongoClient(
            url, tz_aware=True, serverSelectionTimeoutMS=5000
        )
    else:
        cliente = mongomock.MongoClient(tz_aware=True)
    yield cliente
    cliente.close()


@pytest.fixture
def banco(cliente_mongo: MongoClient[dict[str, Any]]) -> BancoMongo:
    cliente_mongo.drop_database(NOME_BANCO)
    return BancoMongo(cliente_mongo, NOME_BANCO)


@pytest.fixture
def relogio() -> RelogioFixo:
    return RelogioFixo(AS_10H)


@pytest.fixture
def principal() -> PrincipalFalso:
    return PrincipalFalso()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        token_servico_entrada=TOKEN_ENTRADA,
        token_servico_saida=TOKEN_SAIDA,
        cpf_chave=CHAVE_CPF,
        principal_url="http://principal.teste",
        prazo_reserva_minutos=30,
        circuito_limite_falhas=3,
        log_level="INFO",
    )


@pytest.fixture
def app(
    settings: Settings, banco: BancoMongo, relogio: RelogioFixo, principal: PrincipalFalso
) -> FastAPI:
    return criar_app(
        settings,
        banco=banco,
        relogio=relogio,
        transporte_principal=httpx.MockTransport(principal),
        dormir=lambda _segundos: None,
    )


@pytest.fixture
def componentes(app: FastAPI) -> Componentes:
    resultado: Componentes = app.state.componentes
    return resultado


@pytest.fixture
def cliente_http(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as cliente:
        yield cliente


@pytest.fixture
def api(cliente_http: TestClient) -> Api:
    return Api(cliente_http)
