"""App factory do serviço de venda. Entrada do container: `uvicorn vendas.main:app`."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from vendas import __version__
from vendas.application.casos_uso import CasosUsoVendas
from vendas.composicao import acoes_metricas_negocio, montar
from vendas.domain.erros import ServicoPrincipalIndisponivelError
from vendas.infrastructure.repositorio_mongo import garantir_indices
from vendas.infrastructure.resiliencia import CircuitBreaker, ClienteResiliente, EstadoCircuito
from vendas.interfaces.router import (
    PRINCIPAL_INDISPONIVEL,
    PROBLEMAS_VENDAS,
    RETRY_AFTER_SEGUNDOS,
    criar_router_interno,
    criar_router_publico,
)
from vendas.shared.auth import exigir_token_servico
from vendas.shared.clock import Clock, RelogioSistema
from vendas.shared.config import Settings
from vendas.shared.errors import (
    registrar_problemas,
    registrar_tratadores_globais,
    resposta_problema,
)
from vendas.shared.eventos import PublicadorEventos, PublicadorEventosLog
from vendas.shared.http import Problema
from vendas.shared.logging import configurar_logs
from vendas.shared.metricas import Metricas, MetricasMiddleware, PublicadorEventosComMetricas
from vendas.shared.middleware import CorrelacaoMiddleware
from vendas.shared.mongo import BancoMongo
from vendas.shared.saude import criar_router_saude
from vendas.shared.tracing import configurar_tracing

_logger = logging.getLogger("vendas.main")

PREFIXO_API = "/api/v1"
PREFIXO_INTERNO = "/internal/v1"
NOME_SERVICO = "revenda-vendas"
DESTINO_PRINCIPAL = "principal"

DESCRICAO = """
Serviço de venda da revenda de veículos (Fase 4): vitrine (veículos à venda e vendidos, do
mais barato ao mais caro), compra por CPF e consulta da compra. Isolado, com MongoDB
próprio: a vitrine responde mesmo com o serviço principal fora do ar.

A compra é coordenada por uma **saga orquestrada**: reserva do anúncio, bloqueio da edição
no catálogo e geração da cobrança no serviço principal, com compensação de cada passo em
caso de falha. O resultado do pagamento chega pelo serviço principal.

As rotas `/internal/v1` não têm rota no Kong e exigem `X-Service-Token`.
Erros seguem a RFC 9457 (`application/problem+json`).
"""

TAGS = [
    {"name": "Vitrine", "description": "Listagens e consulta de anúncio (públicas)."},
    {"name": "Compras", "description": "Compra por CPF e consulta (públicas)."},
    {"name": "Interno: principal", "description": "Réplica de anúncios e resultado do pagamento."},
    {"name": "Saúde", "description": "Probes de liveness e readiness."},
]


def criar_cliente_principal(
    settings: Settings,
    metricas: Metricas | None,
    *,
    transporte: httpx.BaseTransport | None = None,
    dormir: Callable[[float], None] | None = None,
) -> ClienteResiliente:
    def ao_mudar(destino: str, estado: EstadoCircuito) -> None:
        if metricas is not None:
            metricas.circuito.labels(destino=destino).set(int(estado))

    if metricas is not None:
        metricas.circuito.labels(destino=DESTINO_PRINCIPAL).set(int(EstadoCircuito.FECHADO))
    extras: dict[str, Any] = {} if dormir is None else {"dormir": dormir}
    return ClienteResiliente(
        DESTINO_PRINCIPAL,
        settings.principal_url,
        settings.token_servico_saida.get_secret_value(),
        timeout_segundos=settings.http_timeout_segundos,
        tentativas=settings.http_tentativas,
        circuito=CircuitBreaker(
            DESTINO_PRINCIPAL,
            limite_falhas=settings.circuito_limite_falhas,
            tempo_aberto_segundos=settings.circuito_aberto_segundos,
            ao_mudar=ao_mudar,
        ),
        transporte=transporte,
        metricas=metricas,
        **extras,
    )


def criar_app(
    settings: Settings | None = None,
    *,
    banco: BancoMongo | None = None,
    relogio: Clock | None = None,
    publicador: PublicadorEventos | None = None,
    transporte_principal: httpx.BaseTransport | None = None,
    dormir: Callable[[float], None] | None = None,
) -> FastAPI:
    """Monta a aplicação; os parâmetros opcionais permitem substituir adaptadores nos testes."""
    settings = settings or Settings()  # valores vêm do ambiente
    configurar_logs(settings.log_level)

    banco = banco or BancoMongo.conectar(
        settings.mongo_url.get_secret_value(), settings.mongo_banco
    )
    try:
        garantir_indices(banco.db)
    except Exception:  # MongoDB fora na subida: a readiness acusa; o CronJob recria depois
        _logger.exception("não foi possível garantir os índices do MongoDB")
    metricas = Metricas()
    cliente_principal = criar_cliente_principal(
        settings, metricas, transporte=transporte_principal, dormir=dormir
    )
    componentes = montar(
        settings,
        banco,
        cliente_principal,
        relogio or RelogioSistema(),
        PublicadorEventosComMetricas(
            publicador or PublicadorEventosLog(), acoes_metricas_negocio(metricas)
        ),
    )

    def casos_uso() -> CasosUsoVendas:
        return componentes.casos_uso

    @asynccontextmanager
    async def ciclo_de_vida(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            cliente_principal.fechar()

    app = FastAPI(
        title="Revenda de Veículos: serviço de venda",
        version=__version__,
        description=DESCRICAO,
        openapi_tags=TAGS,
        lifespan=ciclo_de_vida,
        docs_url=f"{settings.docs_prefixo}/docs",
        openapi_url=f"{settings.docs_prefixo}/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(MetricasMiddleware, metricas=metricas)
    app.add_middleware(CorrelacaoMiddleware)
    registrar_tratadores_globais(app)
    registrar_problemas(app, PROBLEMAS_VENDAS)

    async def principal_indisponivel(request: Request, exc: Exception) -> JSONResponse:
        return resposta_problema(
            request,
            PRINCIPAL_INDISPONIVEL,
            str(exc),
            headers={"Retry-After": RETRY_AFTER_SEGUNDOS},
        )

    app.add_exception_handler(ServicoPrincipalIndisponivelError, principal_indisponivel)

    exigir_servico = exigir_token_servico(settings.token_servico_entrada.get_secret_value())
    app.include_router(criar_router_publico(casos_uso), prefix=PREFIXO_API)
    app.include_router(criar_router_interno(exigir_servico, casos_uso), prefix=PREFIXO_INTERNO)
    app.include_router(criar_router_saude(banco.disponivel))
    app.add_route("/metrics", metricas.endpoint(), methods=["GET"], include_in_schema=False)

    _incluir_esquema_problema(app)
    configurar_tracing(NOME_SERVICO, app=app)
    app.state.banco = banco
    app.state.metricas = metricas
    app.state.componentes = componentes
    app.state.cliente_principal = cliente_principal
    return app


def _incluir_esquema_problema(app: FastAPI) -> None:
    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            esquema = get_openapi(
                title=app.title,
                version=app.version,
                description=app.description,
                routes=app.routes,
                tags=app.openapi_tags,
            )
            componentes = esquema.setdefault("components", {}).setdefault("schemas", {})
            componentes["Problema"] = Problema.model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
            componentes.update(componentes["Problema"].pop("$defs", {}))
            app.openapi_schema = esquema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


_app: FastAPI | None = None


def __getattr__(nome: str) -> Any:
    # `app` é criado sob demanda (PEP 562): importar vendas.main nos testes não exige as
    # variáveis de ambiente da aplicação; o uvicorn acessa `vendas.main:app`.
    global _app
    if nome == "app":
        if _app is None:
            _app = criar_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {nome!r}")
