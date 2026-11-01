"""Erros HTTP no formato RFC 9457 (application/problem+json) e tratadores globais."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

MEDIA_TYPE_PROBLEMA = "application/problem+json"
PREFIXO_TIPO = "urn:revenda:problema:"

_logger = logging.getLogger("vendas.erros")


@dataclass(frozen=True, slots=True)
class TipoProblema:
    sufixo: str | None
    titulo: str
    status: int

    @property
    def uri(self) -> str:
        # Sem sufixo: problema HTTP genérico (rota inexistente, método não permitido),
        # para o qual a RFC 9457 recomenda "about:blank".
        return f"{PREFIXO_TIPO}{self.sufixo}" if self.sufixo else "about:blank"


# Catálogo de problemas genéricos (docs/05-api.md, seção 1.2). Os específicos de cada
# módulo ficam nas respectivas camadas de interface.
REQUISICAO_MALFORMADA = TipoProblema("requisicao-malformada", "Requisição malformada", 400)
SERVICO_NAO_AUTORIZADO = TipoProblema("servico-nao-autorizado", "Serviço não autorizado", 401)
VALIDACAO = TipoProblema("validacao", "Dados inválidos", 422)
ERRO_INTERNO = TipoProblema("erro-interno", "Erro interno", 500)
INDISPONIVEL = TipoProblema("indisponivel", "Serviço indisponível", 503)


class ProblemaHttpError(Exception):
    """Erro produzido pela própria camada HTTP (token de serviço)."""

    tipo: ClassVar[TipoProblema] = ERRO_INTERNO
    headers: ClassVar[Mapping[str, str] | None] = None

    def __init__(self, detalhe: str) -> None:
        super().__init__(detalhe)
        self.detalhe = detalhe


class ServicoNaoAutorizadoError(ProblemaHttpError):
    tipo = SERVICO_NAO_AUTORIZADO


def obter_request_id(request: Request) -> str | None:
    valor = getattr(request.state, "request_id", None)
    return valor if isinstance(valor, str) else None


def resposta_problema(
    request: Request,
    tipo: TipoProblema,
    detalhe: str,
    *,
    extensoes: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    corpo: dict[str, Any] = {
        "type": tipo.uri,
        "title": tipo.titulo,
        "status": tipo.status,
        "detail": detalhe,
        "instance": request.url.path,
    }
    request_id = obter_request_id(request)
    if request_id:
        corpo["request_id"] = request_id
    if extensoes:
        corpo.update(extensoes)
    cabecalhos = dict(headers or {})
    if request_id:
        cabecalhos["X-Request-ID"] = request_id
    return JSONResponse(
        corpo, status_code=tipo.status, media_type=MEDIA_TYPE_PROBLEMA, headers=cabecalhos
    )


# ---------------------------------------------------------------- validação (422 / 400)

_MENSAGENS = {
    "missing": "campo obrigatório",
    "extra_forbidden": "campo não permitido",
    "string_too_short": "deve ter ao menos {min_length} caractere(s)",
    "string_too_long": "deve ter no máximo {max_length} caracteres",
    "string_type": "deve ser texto",
    "string_unicode": "deve ser texto Unicode válido",
    "string_pattern_mismatch": "formato inválido",
    "greater_than": "deve ser maior que {gt}",
    "greater_than_equal": "deve ser maior ou igual a {ge}",
    "less_than": "deve ser menor que {lt}",
    "less_than_equal": "deve ser menor ou igual a {le}",
    "decimal_max_places": "deve ter no máximo {decimal_places} casas decimais",
    "decimal_max_digits": "deve ter no máximo {max_digits} dígitos",
    "decimal_whole_digits": "deve ter no máximo {whole_digits} dígitos inteiros",
    "decimal_parsing": "deve ser um número decimal",
    "decimal_type": 'deve ser um número decimal (de preferência string, ex.: "79900.00")',
    "finite_number": "deve ser um número finito",
    "float_parsing": "deve ser um número",
    "float_type": "deve ser um número",
    "int_parsing": "deve ser um número inteiro",
    "int_parsing_size": "número inteiro fora do intervalo permitido",
    "int_type": "deve ser um número inteiro",
    "int_from_float": "deve ser um número inteiro",
    "bool_type": "deve ser verdadeiro ou falso",
    "bool_parsing": "deve ser verdadeiro ou falso",
    "uuid_parsing": "deve ser um UUID válido",
    "uuid_type": "deve ser um UUID válido",
    "uuid_version": "deve ser um UUID válido",
    "enum": "deve ser um de: {expected}",
    "literal_error": "deve ser um de: {expected}",
    "model_type": "deve ser um objeto JSON",
    "model_attributes_type": "deve ser um objeto JSON",
    "dict_type": "deve ser um objeto JSON",
    "too_short": "deve ter ao menos {min_length} item(ns)",
    "too_long": "deve ter no máximo {max_length} item(ns)",
    "list_type": "deve ser uma lista",
    "json_type": "deve ser JSON",
    "json_invalid": "JSON inválido",
}
# Erros de validadores próprios (mensagem já em português, prefixada por "Value error, ").
_TIPOS_COM_MENSAGEM_PROPRIA = frozenset({"value_error"})
_MENSAGEM_GENERICA = "valor inválido"


def _mensagem(erro: Mapping[str, Any]) -> str:
    tipo = str(erro.get("type"))
    modelo = _MENSAGENS.get(tipo)
    if modelo is not None:
        contexto = dict(erro.get("ctx") or {})
        if "expected" in contexto:
            # O pydantic enumera as opções em inglês: "'A', 'B' or 'C'".
            contexto["expected"] = str(contexto["expected"]).replace(" or ", " ou ")
        try:
            return modelo.format(**contexto)
        except (KeyError, IndexError):
            return _MENSAGEM_GENERICA
    if tipo in _TIPOS_COM_MENSAGEM_PROPRIA:
        return str(erro.get("msg", _MENSAGEM_GENERICA)).removeprefix("Value error, ")
    # Tipo de erro do pydantic sem tradução: nunca devolver a mensagem em inglês.
    return _MENSAGEM_GENERICA


def _campo(loc: Sequence[Any]) -> str:
    partes = [str(p) for p in loc]
    if partes and partes[0] in ("body", "query", "path", "header"):
        partes = partes[1:]
    return ".".join(partes) or "corpo"


def erros_validacao(erros: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [{"campo": _campo(e.get("loc", ())), "mensagem": _mensagem(e)} for e in erros]


async def _tratar_validacao(request: Request, exc: Exception) -> JSONResponse:
    erros = list(exc.errors()) if isinstance(exc, RequestValidationError) else []
    if any(e.get("type") == "json_invalid" for e in erros):
        return resposta_problema(
            request, REQUISICAO_MALFORMADA, "O corpo da requisição não é um JSON válido."
        )
    return resposta_problema(
        request,
        VALIDACAO,
        "Um ou mais campos são inválidos.",
        extensoes={"erros": erros_validacao(erros)},
    )


async def _tratar_http(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - registro garante o tipo
        return await _tratar_inesperado(request, exc)
    tipo = TipoProblema(None, _titulo_http(exc.status_code), exc.status_code)
    return resposta_problema(request, tipo, str(exc.detail), headers=dict(exc.headers or {}))


def _titulo_http(status: int) -> str:
    return {
        404: "Recurso não encontrado",
        405: "Método não permitido",
        406: "Formato não aceito",
        415: "Tipo de mídia não suportado",
    }.get(status, "Erro HTTP")


async def _tratar_erro_http(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ProblemaHttpError):  # pragma: no cover - registro garante o tipo
        return await _tratar_inesperado(request, exc)
    return resposta_problema(request, exc.tipo, exc.detalhe, headers=exc.headers)


def responder_erro_inesperado(request: Request, exc: BaseException) -> JSONResponse:
    """Registra o erro (uma única vez, com stack trace) e devolve o 500 sem detalhes internos.

    Chamado pelo `CorrelacaoMiddleware`, que captura a exceção antes do
    `ServerErrorMiddleware` do Starlette: assim ela não é relançada para o uvicorn, que
    registraria o mesmo stack trace de novo em `uvicorn.error`.
    """
    _logger.error("erro inesperado: %s", type(exc).__name__, exc_info=exc)
    return resposta_problema(
        request, ERRO_INTERNO, "Ocorreu um erro inesperado. Informe o request_id ao suporte."
    )


async def _tratar_inesperado(request: Request, exc: Exception) -> JSONResponse:
    # Rede de segurança para falhas fora do CorrelacaoMiddleware (ex.: no próprio middleware).
    return responder_erro_inesperado(request, exc)


def registrar_problemas(app: FastAPI, mapeamento: Mapping[type[Exception], TipoProblema]) -> None:
    """Liga exceções de domínio/aplicação de um módulo a tipos de problema HTTP.

    Se a exceção tiver o atributo `erros` (lista de pares campo/mensagem), ele é exposto
    como o membro de extensão `erros`, igual aos erros de validação do Pydantic.
    """

    for classe, tipo in mapeamento.items():

        async def tratar(
            request: Request, exc: Exception, tipo: TipoProblema = tipo
        ) -> JSONResponse:
            extensoes: dict[str, Any] | None = None
            erros = getattr(exc, "erros", None)
            if erros:
                extensoes = {"erros": [{"campo": c, "mensagem": m} for c, m in erros]}
            return resposta_problema(request, tipo, str(exc), extensoes=extensoes)

        app.add_exception_handler(classe, tratar)


def registrar_tratadores_globais(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _tratar_validacao)
    app.add_exception_handler(StarletteHTTPException, _tratar_http)
    app.add_exception_handler(ProblemaHttpError, _tratar_erro_http)
    app.add_exception_handler(Exception, _tratar_inesperado)
