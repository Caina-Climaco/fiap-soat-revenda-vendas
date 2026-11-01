"""Tracing distribuído com OpenTelemetry (ADR-007).

- Só liga o SDK quando `OTEL_EXPORTER_OTLP_ENDPOINT` está definido (no cluster, o Collector
  do namespace `observabilidade`). Sem ele, a API do OpenTelemetry fica no modo no-op: nada
  é exportado e os testes rodam sem Collector.
- Instrumenta FastAPI (spans de servidor), httpx (spans de cliente com `traceparent`) e
  PyMongo (spans de banco).
- Nenhum atributo de span recebe dado pessoal: o CPF nunca vai para span, e a
  instrumentação do PyMongo é configurada para não registrar o comando (que levaria o
  documento da compra).
"""

from __future__ import annotations

import os
from typing import Any

from opentelemetry import propagate, trace

VARIAVEL_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"


def tracing_habilitado() -> bool:
    return bool(os.environ.get(VARIAVEL_ENDPOINT))


def configurar_tracing(nome_servico: str, *, app: Any = None) -> bool:
    """Liga o SDK e as instrumentações; devolve False (sem efeito) se não houver endpoint."""
    if not tracing_habilitado():
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.pymongo import PymongoInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provedor = TracerProvider(resource=Resource.create({"service.name": nome_servico}))
        provedor.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provedor)
        HTTPXClientInstrumentor().instrument()
        # capture_statement=False: o comando do MongoDB levaria o CPF cifrado para o span.
        PymongoInstrumentor().instrument(capture_statement=False)
    if app is not None:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health/.*,metrics")
    return True


def ids_do_trace_atual() -> dict[str, str]:
    """`trace_id` e `span_id` do span ativo (para os logs), ou vazio se não houver trace."""
    span = trace.get_current_span().get_span_context()
    if not span.is_valid:
        return {}
    return {"trace_id": f"{span.trace_id:032x}", "span_id": f"{span.span_id:016x}"}


def contexto_atual() -> dict[str, str]:
    """Cabeçalhos W3C (`traceparent`, `tracestate`) do contexto ativo."""
    portador: dict[str, str] = {}
    propagate.inject(portador)
    return portador


tracer = trace.get_tracer("vendas")
