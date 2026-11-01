"""Instantes, CPFs e dados de exemplo comuns aos testes."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

AS_10H = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
CPF = "529.982.247-25"
CPF_DIGITOS = "52998224725"
CPF_MASCARADO = "***.982.247-**"
CHAVE_CPF = base64.b64encode(bytes(range(32))).decode()
TOKEN_ENTRADA = "token-do-principal-0123456789"
TOKEN_SAIDA = "token-para-o-principal-0123456789"

ANUNCIO = {
    "versao": 1,
    "marca": "Toyota",
    "modelo": "Corolla XEi",
    "ano": 2022,
    "cor": "Prata",
    "preco": "119900.00",
    "criado_em": "2026-11-04T09:00:00+00:00",
}
