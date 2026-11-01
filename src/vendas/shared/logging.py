"""Logs estruturados em JSON (stdout) com correlação por request_id.

Política: nunca registrar o X-Service-Token nem dados pessoais (o CPF nunca vai para o log).
Quando há um trace ativo, cada linha leva `trace_id` e `span_id` (ADR-007).
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from vendas.shared.tracing import ids_do_trace_atual

request_id_atual: ContextVar[str | None] = ContextVar("request_id", default=None)

# Atributos do LogRecord que não vão para o JSON; `color_message` é a cópia com escapes
# ANSI que o uvicorn anexa às próprias mensagens.
_ATRIBUTOS_PADRAO = frozenset(vars(logging.makeLogRecord({})).keys()) | {
    "message",
    "asctime",
    "color_message",
}


class FormatadorJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        registro: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "nivel": record.levelname,
            "logger": record.name,
            "mensagem": record.getMessage(),
        }
        request_id = request_id_atual.get()
        if request_id:
            registro["request_id"] = request_id
        registro.update(ids_do_trace_atual())
        campos = getattr(record, "campos", None)
        if isinstance(campos, dict):
            registro.update(campos)
        for chave, valor in record.__dict__.items():
            if chave not in _ATRIBUTOS_PADRAO and chave != "campos" and not chave.startswith("_"):
                registro.setdefault(chave, valor)
        if record.exc_info:
            registro["excecao"] = self.formatException(record.exc_info)
        return json.dumps(registro, ensure_ascii=False, default=str)


def configurar_logs(nivel: str = "INFO") -> None:
    """Configura o logger raiz com saída JSON; idempotente (pode ser chamada mais de uma vez)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(FormatadorJson())
    raiz = logging.getLogger()
    raiz.handlers[:] = [handler]
    raiz.setLevel(nivel)
    # Os logs do uvicorn passam pelo formatador JSON; o access log dele é substituído pelo
    # middleware de acesso da aplicação (que inclui request_id e latência, sem query string).
    for nome in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(nome)
        logger.handlers.clear()
        logger.propagate = True
    acesso = logging.getLogger("uvicorn.access")
    acesso.handlers.clear()
    acesso.propagate = False
    acesso.disabled = True
