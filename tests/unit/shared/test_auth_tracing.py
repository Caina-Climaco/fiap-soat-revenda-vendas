"""Token de serviço das rotas internas e tracing."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider

from vendas.shared import tracing
from vendas.shared.auth import exigir_token_servico
from vendas.shared.errors import registrar_tratadores_globais

TOKEN = "token-de-servico-0123456789"


@pytest.fixture
def cliente() -> TestClient:
    app = FastAPI()
    registrar_tratadores_globais(app)

    @app.get("/interno", dependencies=[Depends(exigir_token_servico(TOKEN))])
    def interno() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


@pytest.mark.parametrize("token", [None, "", "errado", TOKEN + "x"])
def test_token_de_servico_invalido(cliente: TestClient, token: str | None) -> None:
    cabecalhos = {} if token is None else {"X-Service-Token": token}
    resposta = cliente.get("/interno", headers=cabecalhos)
    assert resposta.status_code == 401
    assert resposta.json()["type"].endswith("servico-nao-autorizado")


def test_token_de_servico_valido(cliente: TestClient) -> None:
    assert cliente.get("/interno", headers={"X-Service-Token": TOKEN}).status_code == 200


# ---------------------------------------------------------------- tracing


def test_tracing_desligado_sem_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tracing.VARIAVEL_ENDPOINT, raising=False)
    assert tracing.tracing_habilitado() is False
    assert tracing.configurar_tracing("teste") is False
    assert tracing.ids_do_trace_atual() == {}
    assert tracing.contexto_atual() == {}


def test_ids_do_trace_e_contexto_propagado() -> None:
    rastreador = TracerProvider().get_tracer("teste")
    with rastreador.start_as_current_span("origem"):
        ids = tracing.ids_do_trace_atual()
        portador = tracing.contexto_atual()
    assert len(ids["trace_id"]) == 32
    assert portador["traceparent"].split("-")[1] == ids["trace_id"]


@pytest.fixture
def provedor_global_isolado(monkeypatch: pytest.MonkeyPatch) -> None:
    """O provedor de traces do OpenTelemetry é global e só pode ser definido uma vez por
    processo: zerado só durante o teste e restaurado no fim, para não vazar para os outros."""
    from opentelemetry import trace
    from opentelemetry.util._once import Once

    # Atributos privados do opentelemetry-api (não há API pública para desfazer o
    # set_tracer_provider); se uma atualização da biblioteca os renomear, este teste quebra
    # na hora e aponta aqui.
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER_SET_ONCE", Once())


@pytest.mark.usefixtures("provedor_global_isolado")
def test_tracing_ligado_instrumenta_app_mongo_e_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry import trace
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.pymongo import PymongoInstrumentor

    monkeypatch.setenv(tracing.VARIAVEL_ENDPOINT, "http://127.0.0.1:9")
    app = FastAPI()
    assert tracing.configurar_tracing("teste", app=app)
    provedor = trace.get_tracer_provider()
    try:
        assert isinstance(provedor, TracerProvider)
        # Uma segunda chamada (outro app no mesmo processo) reaproveita o provedor.
        assert tracing.configurar_tracing("teste", app=FastAPI())
        assert trace.get_tracer_provider() is provedor
        # Sem app (o CronJob de recuperação): só o provedor, o httpx e o PyMongo.
        assert tracing.configurar_tracing("teste")
    finally:
        HTTPXClientInstrumentor().uninstrument()
        PymongoInstrumentor().uninstrument()
        provedor.shutdown()  # type: ignore[union-attr]


def test_provedor_global_volta_ao_padrao_depois_do_teste_de_tracing() -> None:
    from opentelemetry import trace

    assert not isinstance(trace.get_tracer_provider(), TracerProvider)
