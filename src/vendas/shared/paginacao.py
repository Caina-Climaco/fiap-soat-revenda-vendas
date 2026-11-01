"""Página de resultados devolvida pelos casos de uso de listagem."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

LIMITE_PADRAO = 20
LIMITE_MAXIMO = 100
# Teto do deslocamento: valores maiores que um BIGINT do PostgreSQL causavam 500 no OFFSET,
# e nenhuma listagem legítima pula mais de um milhão de itens.
DESLOCAMENTO_MAXIMO = 1_000_000


@dataclass(frozen=True, slots=True)
class Pagina[T]:
    itens: Sequence[T]
    total: int
    limite: int
    deslocamento: int
