"""Métricas Prometheus do serviço de venda: HTTP, negócio (eventos), saga e resiliência.

Desenho:
- `Metricas` agrupa os instrumentos num `CollectorRegistry` próprio de cada app (nada no
  registry global do prometheus-client): testes e apps criados lado a lado não colidem.
- `MetricasMiddleware` (ASGI puro) mede toda requisição HTTP com rótulos de baixa
  cardinalidade: método, template da rota (`/api/v1/compras/{compra_id}`, nunca o id) e status.
- Os contadores de negócio são alimentados por `PublicadorEventosComMetricas`, um decorador
  do `PublicadorEventos` já existente: os eventos de domínio só são publicados depois do
  commit, então os contadores registram apenas fatos confirmados. Este módulo não conhece
  os módulos de negócio; a tabela "nome do evento → contador" é montada na composição.
- Nenhum rótulo carrega dado pessoal ou identificador de recurso.

`/metrics` é público no ambiente local (scrape sem autenticação). Em produção ele não deve
ser exposto fora do cluster: restringir por NetworkPolicy/Service interno (porta separada)
ou proteger com autenticação no proxy. Um processo por pod (sem modo multiprocess).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    disable_created_metrics,
    generate_latest,
)
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vendas.shared.eventos import EventoDominio, PublicadorEventos
from vendas.shared.middleware import rota_template

ROTA_NAO_MAPEADA = "nao_mapeada"
# Faixas (s) pensadas para uma API síncrona com banco: de 5 ms a 5 s.
_FAIXAS_LATENCIA = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class Metricas:
    """Instrumentos da aplicação, registrados num registry exclusivo."""

    def __init__(self) -> None:
        # Sem as séries auxiliares *_created (timestamp de criação de cada contador).
        disable_created_metrics()  # type: ignore[no-untyped-call]  # função sem anotações na lib
        self.registry = CollectorRegistry(auto_describe=True)
        ProcessCollector(registry=self.registry)
        PlatformCollector(registry=self.registry)
        GCCollector(registry=self.registry)

        rotulos_http = ("metodo", "rota", "status")
        self.http_requisicoes = Counter(
            "revenda_http_requisicoes",
            "Requisições HTTP atendidas.",
            rotulos_http,
            registry=self.registry,
        )
        self.http_duracao = Histogram(
            "revenda_http_requisicao_duracao_segundos",
            "Latência das requisições HTTP (segundos).",
            rotulos_http,
            buckets=_FAIXAS_LATENCIA,
            registry=self.registry,
        )
        self.compras_iniciadas = Counter(
            "revenda_compras_iniciadas",
            "Compras iniciadas (saga começou).",
            registry=self.registry,
        )
        self.compras_aguardando = Counter(
            "revenda_compras_aguardando_pagamento",
            "Compras que chegaram à espera do pagamento (T1, T2 e T3 concluídos).",
            registry=self.registry,
        )
        self.compras_efetivadas = Counter(
            "revenda_compras_efetivadas",
            "Compras efetivadas (pagamento efetuado).",
            registry=self.registry,
        )
        self.compras_canceladas = Counter(
            "revenda_compras_canceladas",
            "Compras canceladas, por motivo.",
            ("motivo",),
            registry=self.registry,
        )
        self.passos_saga = Counter(
            "revenda_saga_passos",
            "Passos e compensações da saga, por resultado (concluído, compensado, falha...).",
            ("passo", "resultado"),
            registry=self.registry,
        )
        self.anuncios_replicados = Counter(
            "revenda_anuncios_replicados",
            "Mensagens de réplica de anúncio recebidas, por efeito (aplicada ou ignorada).",
            ("efeito",),
            registry=self.registry,
        )
        self.chamadas_participante = Counter(
            "revenda_chamadas_participante",
            "Chamadas ao serviço principal, por resultado (sucesso, recusa, nova_tentativa, "
            "falha, circuito_aberto).",
            ("destino", "resultado"),
            registry=self.registry,
        )
        self.sagas_paradas = Gauge(
            "revenda_sagas_paradas",
            "Compras com passo ou compensação pendente sem progresso além do limite de alerta "
            "da recuperação de sagas (calculado por ela a cada coleta).",
            registry=self.registry,
        )
        self.circuito = Gauge(
            "revenda_circuito_estado",
            "Estado do circuit breaker por destino: 0 fechado, 1 meio-aberto, 2 aberto.",
            ("destino",),
            registry=self.registry,
        )

    def exportar(self) -> bytes:
        return generate_latest(self.registry)

    def endpoint(self) -> Callable[[Request], Response]:
        """Rota `GET /metrics` no formato de exposição texto do Prometheus."""

        def metrics(_request: Request) -> Response:
            return Response(self.exportar(), media_type=CONTENT_TYPE_LATEST)

        return metrics


class MetricasMiddleware:
    def __init__(self, app: ASGIApp, metricas: Metricas) -> None:
        self.app = app
        self._metricas = metricas

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inicio = time.perf_counter()
        status = 500  # exceção não tratada que atravessa este middleware

        async def enviar(mensagem: Message) -> None:
            nonlocal status
            if mensagem["type"] == "http.response.start":
                status = mensagem["status"]
            await send(mensagem)

        try:
            await self.app(scope, receive, enviar)
        finally:
            # "route" só existe no scope quando o roteador encontrou a rota; caminhos
            # inexistentes (404) viram um rótulo fixo para não explodir a cardinalidade.
            rota = rota_template(scope) if "route" in scope else ROTA_NAO_MAPEADA
            rotulos = (str(scope.get("method", "")), rota, str(status))
            self._metricas.http_requisicoes.labels(*rotulos).inc()
            self._metricas.http_duracao.labels(*rotulos).observe(time.perf_counter() - inicio)


# Ação de métrica para um evento de domínio (recebe o evento já confirmado).
AcaoMetrica = Callable[[EventoDominio], None]


class PublicadorEventosComMetricas:
    """Decorador de `PublicadorEventos`: repassa os eventos e alimenta os contadores."""

    def __init__(self, interno: PublicadorEventos, acoes: Mapping[str, AcaoMetrica]) -> None:
        self._interno = interno
        self._acoes = dict(acoes)

    def publicar(self, eventos: Iterable[EventoDominio]) -> None:
        lista = list(eventos)
        self._interno.publicar(lista)
        for evento in lista:
            acao = self._acoes.get(evento.nome)
            if acao is not None:
                acao(evento)


def rotulo_do_evento(evento: EventoDominio, campo: str, padrao: str = "DESCONHECIDO") -> str:
    """Valor de um campo do evento para uso como rótulo (ex.: o motivo do cancelamento)."""
    dados: Mapping[str, Any] = evento.como_dict()
    return str(dados.get(campo) or padrao)
