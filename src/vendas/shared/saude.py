"""Endpoints de saúde para as probes do Kubernetes (fora do prefixo /api/v1)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from vendas.shared.errors import INDISPONIVEL, MEDIA_TYPE_PROBLEMA, resposta_problema


class Vivo(BaseModel):
    status: Literal["ok"] = "ok"


class Verificacoes(BaseModel):
    banco: Literal["ok", "falha"]


class Pronto(BaseModel):
    status: Literal["ok"] = "ok"
    verificacoes: Verificacoes


def criar_router_saude(verificar_banco: Callable[[], bool]) -> APIRouter:
    router = APIRouter(prefix="/health", tags=["Saúde"])

    @router.get("/live", summary="Liveness: o processo está de pé", response_model=Vivo)
    def vivo() -> Vivo:
        return Vivo()

    @router.get(
        "/ready",
        summary="Readiness: a instância consegue falar com o MongoDB",
        response_model=Pronto,
        responses={
            503: {
                "description": "Banco indisponível",
                "content": {MEDIA_TYPE_PROBLEMA: {}},
            }
        },
    )
    def pronto(request: Request) -> Pronto | JSONResponse:
        if verificar_banco():
            return Pronto(verificacoes=Verificacoes(banco="ok"))
        return resposta_problema(
            request,
            INDISPONIVEL,
            "O MongoDB não está acessível.",
            extensoes={"verificacoes": {"banco": "falha"}},
        )

    return router
