"""Provedor: o contrato publicado em contratos/ e exatamente o da aplicacao atual."""

from __future__ import annotations

import uuid

import pytest

from contrato.gerar import ARQUIVO, contrato_atual
from contrato.openapi_contrato import (
    DIRETORIO,
    Contrato,
    ViolacaoDeContratoError,
    carregar,
    serializar,
)

pytestmark = pytest.mark.contrato


def test_contrato_publicado_e_o_da_aplicacao() -> None:
    publicado = (DIRETORIO / ARQUIVO).read_text(encoding="utf-8")
    assert serializar(contrato_atual()) == publicado, (
        "As rotas internas mudaram: rode `uv run python -m contrato.gerar` (PYTHONPATH=tests), "
        "versione o contrato e atualize a copia no repositorio principal."
    )


def test_validador_detecta_quebra_de_contrato() -> None:
    """O validador falha quando o corpo foge do contrato (garantia do proprio teste)."""
    principal = Contrato(carregar("principal-interno.openapi.json"))
    with pytest.raises(ViolacaoDeContratoError, match="fora do contrato"):
        principal.validar_requisicao(
            "POST", f"/internal/v1/veiculos/{uuid.uuid4()}/bloqueio", {"versao": "1"}
        )
    with pytest.raises(ViolacaoDeContratoError, match="rota fora do contrato"):
        principal.validar_requisicao("POST", "/internal/v1/veiculos", None)
