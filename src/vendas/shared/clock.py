"""Relógio injetável: todo "agora" do sistema passa por aqui (testes controlam o tempo)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def agora(self) -> datetime:
        """Instante atual em UTC (timezone-aware)."""
        ...


class RelogioSistema:
    def agora(self) -> datetime:
        return datetime.now(UTC)


class RelogioFixo:
    """Relógio controlado manualmente, usado nos testes."""

    def __init__(self, instante: datetime) -> None:
        if instante.tzinfo is None:
            raise ValueError("RelogioFixo exige datetime com timezone")
        self._instante = instante.astimezone(UTC)

    def agora(self) -> datetime:
        return self._instante

    def definir(self, instante: datetime) -> None:
        self._instante = instante.astimezone(UTC)

    def avancar(self, delta: timedelta) -> None:
        self._instante += delta
