"""Autorização das rotas internas (ADR-005 da solução): `X-Service-Token`.

As rotas públicas do serviço de venda (vitrine e compra) não exigem credencial; o Kong
aplica o *rate limiting*. As rotas internas não têm rota no Kong, a NetworkPolicy só deixa
o serviço principal alcançá-las e cada chamada apresenta o token de serviço.
"""

# Sem `from __future__ import annotations` neste módulo: a dependência usa
# `Annotated[..., Depends(esquema)]` com o esquema local à fábrica, e o FastAPI só o resolve
# se as anotações forem avaliadas na definição da função.

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import APIKeyHeader

from vendas.shared.errors import ServicoNaoAutorizadoError

CABECALHO_TOKEN_SERVICO = "X-Service-Token"  # noqa: S105 - nome do header, não segredo


def exigir_token_servico(token: str) -> Callable[[str | None], None]:
    """Dependência das rotas internas: exige `X-Service-Token` igual ao segredo configurado."""
    esquema = APIKeyHeader(
        name=CABECALHO_TOKEN_SERVICO,
        scheme_name="token-servico",
        description="Segredo compartilhado entre os serviços (rotas internas).",
        auto_error=False,
    )
    esperado = token.encode()

    def dependencia(recebido: Annotated[str | None, Depends(esquema)]) -> None:
        if not recebido or not hmac.compare_digest(recebido.encode(), esperado):
            raise ServicoNaoAutorizadoError("Token de serviço ausente ou inválido.")

    return dependencia
