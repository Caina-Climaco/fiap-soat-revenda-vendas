"""Portas de persistência de Vendas (implementadas em vendas/infrastructure com MongoDB).

Cada operação que muda estado é atômica num único documento: não há transação entre
documentos. O que ficar pela metade entre dois documentos (anúncio e compra) é resolvido
pela saga, que conhece os passos iniciados e é retomada pela recuperação.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from vendas.domain.anuncio import Anuncio, DadosAnuncio, StatusAnuncio
from vendas.domain.compra import Compra, ResultadoPagamento


class ResultadoReserva(StrEnum):
    RESERVADO = "RESERVADO"
    NAO_ENCONTRADO = "NAO_ENCONTRADO"
    VERSAO_DIVERGENTE = "VERSAO_DIVERGENTE"
    INDISPONIVEL = "INDISPONIVEL"


class CompraAtivaExistenteError(Exception):
    """Já existe compra ativa para o veículo (índice único parcial, RN-03)."""


class ConflitoRevisaoError(Exception):
    """O documento da compra mudou desde a leitura (controle otimista pela `revisao`)."""


class AnuncioRepository(Protocol):
    def aplicar_replica(self, dados: DadosAnuncio, agora: datetime) -> bool:
        """Cria ou atualiza os dados públicos se a versão for maior; nunca mexe na
        disponibilidade. False quando a versão recebida não é mais nova (ADR-003)."""
        ...

    def obter(self, veiculo_id: UUID) -> Anuncio | None: ...

    def reservar(
        self, veiculo_id: UUID, versao: int, compra_id: UUID, agora: datetime
    ) -> ResultadoReserva:
        """T1: A_VENDA → RESERVADO se a versão for a informada. Repetir para a mesma
        compra devolve RESERVADO (idempotente)."""
        ...

    def liberar(self, veiculo_id: UUID, compra_id: UUID, agora: datetime) -> bool:
        """C1: RESERVADO pela compra → A_VENDA. Sem reserva dessa compra, sem efeito."""
        ...

    def vender(
        self, veiculo_id: UUID, compra_id: UUID, preco: Decimal, data_venda: datetime
    ) -> bool:
        """T5: RESERVADO pela compra → VENDIDO, com preço e data da venda."""
        ...

    def listar(
        self, status: StatusAnuncio, *, limite: int, deslocamento: int
    ) -> tuple[Sequence[Anuncio], int]:
        """À venda: por preço; vendidos: por preço da venda (RN-17, RN-20)."""
        ...


class CompraRepository(Protocol):
    def inserir(self, compra: Compra) -> None:
        """Grava a compra nova; CompraAtivaExistenteError se já houver ativa no veículo."""
        ...

    def obter(self, compra_id: UUID) -> Compra | None: ...

    def salvar(self, compra: Compra) -> None:
        """Grava se a `revisao` não mudou (incrementa); senão ConflitoRevisaoError."""
        ...

    def registrar_resultado(
        self, compra_id: UUID, resultado: ResultadoPagamento, codigo: str, agora: datetime
    ) -> bool:
        """Grava o resultado se ainda não houver um (atômico, sem lease)."""
        ...

    def tomar_lease(
        self, compra_id: UUID, dono: str, agora: datetime, duracao: timedelta
    ) -> Compra | None:
        """Toma posse da saga se ninguém a tiver (ou se o lease venceu). None se ocupada."""
        ...

    def liberar_lease(self, compra_id: UUID, dono: str) -> None: ...

    def pendentes(self, agora: datetime, limite: int) -> list[UUID]:
        """Compras cuja saga tem algo a fazer agora (ADR-006 do serviço de venda)."""
        ...

    def canceladas_com_cpf_ate(self, limite_data: datetime, limite: int) -> list[UUID]:
        """Compras canceladas antes de `limite_data` que ainda guardam o CPF."""
        ...
