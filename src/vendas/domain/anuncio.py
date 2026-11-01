"""Anúncio: réplica de leitura do veículo, mais a disponibilidade (ADR-003 do serviço de venda).

Os dados descritivos e a versão vêm do Catálogo (serviço principal); a disponibilidade é
deste serviço. As mudanças de estado são operações atômicas de um único documento no
repositório (reservar, liberar, vender); aqui ficam o modelo e as regras de leitura.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class StatusAnuncio(StrEnum):
    A_VENDA = "A_VENDA"
    RESERVADO = "RESERVADO"
    VENDIDO = "VENDIDO"


@dataclass(frozen=True, slots=True)
class DadosAnuncio:
    """O que chega na réplica: dados públicos do veículo e a versão."""

    veiculo_id: UUID
    versao: int
    marca: str
    modelo: str
    ano: int
    cor: str
    preco: Decimal
    criado_em: datetime


@dataclass(frozen=True, slots=True)
class Anuncio:
    veiculo_id: UUID
    versao: int
    marca: str
    modelo: str
    ano: int
    cor: str
    preco: Decimal
    status: StatusAnuncio
    criado_em: datetime
    atualizado_em: datetime
    compra_id: UUID | None = None
    preco_venda: Decimal | None = None
    data_venda: datetime | None = None

    @classmethod
    def novo(cls, dados: DadosAnuncio, agora: datetime) -> Anuncio:
        """Veículo nasce à venda e entra na vitrine assim que a réplica chega (RN-16)."""
        return cls(
            veiculo_id=dados.veiculo_id,
            versao=dados.versao,
            marca=dados.marca,
            modelo=dados.modelo,
            ano=dados.ano,
            cor=dados.cor,
            preco=dados.preco,
            status=StatusAnuncio.A_VENDA,
            criado_em=dados.criado_em,
            atualizado_em=agora,
        )
