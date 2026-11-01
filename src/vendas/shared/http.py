"""Convenções HTTP comuns (docs/05-api.md, seção 1): datas, dinheiro e paginação."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, PlainSerializer, WithJsonSchema

from vendas.shared.errors import MEDIA_TYPE_PROBLEMA
from vendas.shared.paginacao import DESLOCAMENTO_MAXIMO, LIMITE_MAXIMO, LIMITE_PADRAO

_DESCRICOES_STATUS = {
    400: "Requisição malformada (JSON inválido)",
    401: "Token de serviço ausente ou inválido",
    404: "Recurso não encontrado",
    409: "Conflito de estado",
    422: "Dados inválidos",
}


def respostas_problema(*status: int, **descricoes: str) -> dict[int | str, dict[str, object]]:
    """Documenta no OpenAPI as respostas de erro `application/problem+json` de uma rota.

    `descricoes` permite detalhar um código: `respostas_problema(404, s404="Veículo inexistente")`.
    """
    esquema = {"$ref": "#/components/schemas/Problema"}
    return {
        codigo: {
            "description": descricoes.get(f"s{codigo}", _DESCRICOES_STATUS.get(codigo, "Erro")),
            "content": {MEDIA_TYPE_PROBLEMA: {"schema": esquema}},
        }
        for codigo in status
    }


def _data_hora_utc(valor: datetime) -> str:
    return valor.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dinheiro(valor: Decimal) -> str:
    return f"{valor.quantize(Decimal('0.01'))}"


# ISO-8601 em UTC, sufixo Z, precisão de segundos.
DataHoraUtc = Annotated[
    datetime,
    PlainSerializer(_data_hora_utc, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time", "examples": ["2026-10-03T14:05:00Z"]}),
]

# Valor monetário como string decimal com duas casas (nunca float).
Dinheiro = Annotated[
    Decimal,
    PlainSerializer(_dinheiro, return_type=str),
    WithJsonSchema({"type": "string", "pattern": r"^\d{1,10}\.\d{2}$", "examples": ["124900.00"]}),
]


class ModeloResposta(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ModeloRequisicao(BaseModel):
    # Campos desconhecidos ou somente leitura (status, versao...) resultam em 422.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErroCampo(BaseModel):
    campo: str
    mensagem: str


class Problema(BaseModel):
    """Problem Details (RFC 9457) com as extensões `request_id` e `erros`."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    request_id: str | None = None
    erros: list[ErroCampo] | None = None


@dataclass(frozen=True, slots=True)
class ParametrosPaginacao:
    limite: int
    deslocamento: int


def paginacao(
    limite: Annotated[
        int, Query(ge=1, le=LIMITE_MAXIMO, description="Itens por página (1 a 100).")
    ] = LIMITE_PADRAO,
    deslocamento: Annotated[
        int,
        Query(
            ge=0,
            le=DESLOCAMENTO_MAXIMO,
            description="Quantidade de itens a pular (0 a 1.000.000).",
        ),
    ] = 0,
) -> ParametrosPaginacao:
    return ParametrosPaginacao(limite=limite, deslocamento=deslocamento)
