"""Logs estruturados em JSON (stdout) com correlação por request_id.

Política: nunca registrar o X-Service-Token nem dados pessoais (o CPF nunca vai para o log).
Quando há um trace ativo, cada linha leva `trace_id` e `span_id` (ADR-007).

Segunda barreira (ADR-005 do serviço de venda): todo valor da linha passa por um filtro que
mascara qualquer sequência com cara de CPF (11 dígitos, com ou sem pontuação), venha de onde
vier (mensagem, campo extra ou traceback de exceção). O filtro roda sobre os valores, antes da
serialização, para que a linha continue sendo JSON válido: um inteiro de 11 dígitos vira a
string mascarada, nunca um trecho sem aspas.
"""

from __future__ import annotations

import json
import logging
import re
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


# 11 dígitos no formato do CPF, isolados: não pega trechos de números maiores nem de
# identificadores hexadecimais (trace_id, uuid), que têm letras ou dígitos colados.
_PADRAO_CPF = re.compile(r"(?<![\w.\-])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?![\w\-]|\.\d)")
CPF_MASCARADO = "***.***.***-**"


def mascarar_cpf(texto: str) -> str:
    return _PADRAO_CPF.sub(CPF_MASCARADO, texto)


def _mascarar_valor(valor: Any) -> Any:
    """Aplica o filtro a cada valor da linha, recursivamente, preservando os tipos do JSON."""
    if isinstance(valor, str):
        return mascarar_cpf(valor)
    if isinstance(valor, bool) or valor is None or isinstance(valor, float):
        return valor
    if isinstance(valor, int):
        return CPF_MASCARADO if _PADRAO_CPF.fullmatch(str(valor)) else valor
    if isinstance(valor, dict):
        return {str(chave): _mascarar_valor(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple, set, frozenset)):
        return [_mascarar_valor(item) for item in valor]
    return mascarar_cpf(str(valor))


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
        return json.dumps(_mascarar_valor(registro), ensure_ascii=False)


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
