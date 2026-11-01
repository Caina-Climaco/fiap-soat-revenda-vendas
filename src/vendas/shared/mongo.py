"""Conexão com o MongoDB (ADR-001 do serviço de venda)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import MongoClient
from pymongo.database import Database


class BancoMongo:
    """Cliente (thread-safe, com pool próprio) e o banco da aplicação."""

    def __init__(self, cliente: MongoClient[dict[str, Any]], nome: str) -> None:
        self.cliente = cliente
        self.db: Database[dict[str, Any]] = cliente[nome]

    @classmethod
    def conectar(cls, url: str, nome: str) -> BancoMongo:
        cliente: MongoClient[dict[str, Any]] = MongoClient(
            url,
            tz_aware=True,
            # Readiness e chamadas falham rápido quando o banco está fora.
            serverSelectionTimeoutMS=2000,
            connectTimeoutMS=2000,
            appname="revenda-vendas",
        )
        return cls(cliente, nome)

    def disponivel(self) -> bool:
        try:
            self.cliente.admin.command("ping")
        except Exception:  # qualquer falha de conexão = não pronto
            return False
        return True

    def fechar(self) -> None:
        self.cliente.close()


def utc(valor: datetime | None) -> datetime | None:
    """Datas lidas do MongoDB em UTC com fuso (o driver real devolve, o simulado não)."""
    if valor is None:
        return None
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=UTC)
