"""Middleware ASGI de correlação (X-Request-ID) e log de acesso estruturado."""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vendas.shared.errors import responder_erro_inesperado
from vendas.shared.logging import request_id_atual

_logger = logging.getLogger("vendas.acesso")

CABECALHO = "X-Request-ID"
# Aceita apenas identificadores "comportados" vindos do cliente (evita injeção em logs).
_ID_VALIDO = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class CorrelacaoMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _extrair_request_id(scope) or str(uuid.uuid4())
        # Em scope["state"] (= request.state) para que os tratadores de erro, inclusive o
        # de 500 que roda fora deste middleware, consigam incluir o request_id na resposta.
        estado: dict[str, Any] = scope.setdefault("state", {})
        estado["request_id"] = request_id
        token = request_id_atual.set(request_id)
        inicio = time.perf_counter()
        status = 500
        iniciada = False

        async def enviar(mensagem: Message) -> None:
            nonlocal status, iniciada
            if mensagem["type"] == "http.response.start":
                status = mensagem["status"]
                iniciada = True
                headers = MutableHeaders(scope=mensagem)
                headers[CABECALHO] = request_id
            await send(mensagem)

        try:
            await self.app(scope, receive, enviar)
        except Exception as exc:
            if iniciada:  # pragma: no cover - resposta já começou; nada a fazer além de relançar
                raise
            # Responde aqui (com request_id no contexto do log) em vez de deixar a exceção
            # subir até o ServerErrorMiddleware, que a relançaria para o uvicorn registrar
            # um segundo stack trace.
            resposta = responder_erro_inesperado(Request(scope), exc)
            await resposta(scope, receive, enviar)
        finally:
            campos: dict[str, Any] = {
                "metodo": scope.get("method"),
                "rota": rota_template(scope),
                "status": status,
                "latencia_ms": round((time.perf_counter() - inicio) * 1000, 1),
            }
            _logger.log(nivel_acesso(status), "requisição atendida", extra={"campos": campos})
            request_id_atual.reset(token)


def _extrair_request_id(scope: Scope) -> str | None:
    for nome, valor in scope.get("headers", []):
        if nome == b"x-request-id":
            texto = valor.decode("latin-1").strip()
            return texto if _ID_VALIDO.match(texto) else None
    return None


def nivel_acesso(status: int) -> int:
    """ERROR para falhas do servidor, WARNING para 401/403 (sinal de abuso), INFO no resto."""
    if status >= 500:
        return logging.ERROR
    if status in (401, 403):
        return logging.WARNING
    return logging.INFO


def rota_template(scope: Scope) -> str:
    """Template da rota (/api/v1/vendas/{venda_id}) em vez do caminho concreto.

    É reconstruído a partir de `path` e `path_params` porque, com routers incluídos, o
    `scope["route"]` do FastAPI aponta para a rota original, sem os prefixos. A query
    string nunca é registrada.
    """
    caminho = str(scope.get("path", ""))
    parametros: dict[str, Any] = scope.get("path_params") or {}
    if not parametros:
        return caminho
    nomes_por_valor = {str(valor): nome for nome, valor in parametros.items()}
    return "/".join(
        f"{{{nomes_por_valor[s]}}}" if s in nomes_por_valor else s for s in caminho.split("/")
    )
