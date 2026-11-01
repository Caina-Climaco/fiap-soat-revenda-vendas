"""Eventos de domínio: publicados só em processo, como log estruturado (sem broker)."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol

_logger = logging.getLogger("vendas.eventos")


class EventoDominio(Protocol):
    @property
    def nome(self) -> str: ...

    def como_dict(self) -> dict[str, str]: ...


class PublicadorEventos(Protocol):
    def publicar(self, eventos: Iterable[EventoDominio]) -> None:
        """Chamado somente após o commit, para não registrar eventos de transações desfeitas."""
        ...


class PublicadorEventosLog:
    def publicar(self, eventos: Iterable[EventoDominio]) -> None:
        for evento in eventos:
            _logger.info(
                "evento de domínio %s",
                evento.nome,
                extra={"campos": {"evento": evento.nome, **evento.como_dict()}},
            )
