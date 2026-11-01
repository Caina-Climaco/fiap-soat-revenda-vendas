"""Esquemas HTTP do serviço de venda (docs/05-api.md da solução, seções 5.5 e 5.7)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from vendas.domain.anuncio import Anuncio, StatusAnuncio
from vendas.domain.compra import Compra, MotivoCancelamento, StatusCompra
from vendas.shared.http import DataHoraUtc, Dinheiro, ModeloRequisicao, ModeloResposta


class AnuncioVitrine(ModeloResposta):
    veiculo_id: UUID
    marca: str
    modelo: str
    ano: int
    cor: str
    preco: Dinheiro
    versao: int = Field(description="Envie esta versão no pedido de compra (RN-12).")


class AnuncioVendido(ModeloResposta):
    veiculo_id: UUID
    marca: str
    modelo: str
    ano: int
    cor: str
    preco_venda: Dinheiro
    data_venda: DataHoraUtc


class AnuncioResposta(AnuncioVitrine):
    status: StatusAnuncio


class PaginaVitrine(ModeloResposta):
    itens: list[AnuncioVitrine]
    total: int
    limite: int
    deslocamento: int


class PaginaVendidos(ModeloResposta):
    itens: list[AnuncioVendido]
    total: int
    limite: int
    deslocamento: int


class CompraRequisicao(ModeloRequisicao):
    veiculo_id: UUID
    versao: int = Field(
        ge=1, strict=True, description="Versão do anúncio exibida na vitrine (inteiro JSON)."
    )
    cpf: str = Field(
        min_length=11,
        max_length=14,
        description="CPF do comprador, com ou sem pontuação.",
        examples=["529.982.247-25"],
    )


class VeiculoDaCompra(ModeloResposta):
    marca: str
    modelo: str
    ano: int
    cor: str


class CompraResposta(ModeloResposta):
    id: UUID
    veiculo_id: UUID
    veiculo: VeiculoDaCompra
    preco: Dinheiro
    cpf: str | None = Field(description="Sempre mascarado; nulo após o prazo de retenção.")
    status: StatusCompra
    codigo_pagamento: str | None
    criada_em: DataHoraUtc
    expira_em: DataHoraUtc
    data_venda: DataHoraUtc | None
    motivo_cancelamento: MotivoCancelamento | None

    @classmethod
    def de(cls, compra: Compra) -> CompraResposta:
        return cls(
            id=compra.id,
            veiculo_id=compra.veiculo_id,
            veiculo=VeiculoDaCompra(
                marca=compra.marca, modelo=compra.modelo, ano=compra.ano, cor=compra.cor
            ),
            preco=compra.preco,
            cpf=compra.cpf_mascarado,
            status=compra.status,
            # O código só faz sentido enquanto o pagamento é possível ou depois de pago.
            codigo_pagamento=compra.codigo_pagamento
            if compra.status in (StatusCompra.AGUARDANDO_PAGAMENTO, StatusCompra.EFETIVADA)
            else None,
            criada_em=compra.criada_em,
            expira_em=compra.expira_em,
            data_venda=compra.data_venda,
            motivo_cancelamento=compra.motivo_cancelamento,
        )


# ---------------------------------------------------------------- rotas internas


class ReplicaAnuncio(ModeloRequisicao):
    versao: int = Field(ge=1, strict=True)
    marca: str = Field(min_length=1, max_length=60)
    modelo: str = Field(min_length=1, max_length=60)
    ano: int = Field(ge=1950, le=2100, strict=True)
    cor: str = Field(min_length=1, max_length=30)
    preco: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    criado_em: datetime


class ReplicaAplicada(ModeloResposta):
    aplicado: bool = Field(description="False quando a versão recebida não era mais nova.")


class ResultadoPagamentoRequisicao(ModeloRequisicao):
    codigo_pagamento: str = Field(pattern=r"^PAG-[0-9a-f]{12}$")
    status: Literal["PAGO", "CANCELADO"]


def anuncio_vitrine(anuncio: Anuncio) -> AnuncioVitrine:
    return AnuncioVitrine.model_validate(anuncio)


def anuncio_vendido(anuncio: Anuncio) -> AnuncioVendido:
    return AnuncioVendido(
        veiculo_id=anuncio.veiculo_id,
        marca=anuncio.marca,
        modelo=anuncio.modelo,
        ano=anuncio.ano,
        cor=anuncio.cor,
        preco_venda=anuncio.preco_venda or anuncio.preco,
        data_venda=anuncio.data_venda or anuncio.atualizado_em,
    )
