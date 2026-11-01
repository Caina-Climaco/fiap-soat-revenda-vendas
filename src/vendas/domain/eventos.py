"""Eventos de domínio de Vendas (log estruturado e métricas). Nunca carregam o CPF."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EventoVendas:
    NOME: ClassVar[str] = "EventoVendas"

    compra_id: UUID
    veiculo_id: UUID
    ocorrido_em: datetime

    @property
    def nome(self) -> str:
        return self.NOME

    def como_dict(self) -> dict[str, str]:
        return {
            "compra_id": str(self.compra_id),
            "veiculo_id": str(self.veiculo_id),
            "ocorrido_em": self.ocorrido_em.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CompraIniciada(EventoVendas):
    NOME: ClassVar[str] = "CompraIniciada"


@dataclass(frozen=True, slots=True)
class CompraAguardandoPagamento(EventoVendas):
    NOME: ClassVar[str] = "CompraAguardandoPagamento"


@dataclass(frozen=True, slots=True)
class CompraEfetivada(EventoVendas):
    NOME: ClassVar[str] = "CompraEfetivada"


@dataclass(frozen=True, slots=True)
class CompraCancelada(EventoVendas):
    NOME: ClassVar[str] = "CompraCancelada"

    motivo: str = ""

    def como_dict(self) -> dict[str, str]:
        return {**EventoVendas.como_dict(self), "motivo": self.motivo}


@dataclass(frozen=True, slots=True)
class PassoSagaExecutado(EventoVendas):
    """Um passo ou compensação da saga concluído (para log, métrica e diagnóstico)."""

    NOME: ClassVar[str] = "PassoSagaExecutado"

    passo: str = ""
    resultado: str = ""

    def como_dict(self) -> dict[str, str]:
        return {**EventoVendas.como_dict(self), "passo": self.passo, "resultado": self.resultado}
