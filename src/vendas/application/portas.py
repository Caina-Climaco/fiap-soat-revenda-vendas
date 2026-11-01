"""Portas para os participantes remotos da saga (serviço principal) e para a cifra do CPF.

Os adaptadores HTTP (vendas/infrastructure/participantes.py) traduzem as respostas:
- recusa de negócio (409/404 com `type` do problema) → `RecusaDoParticipanteError`;
- rede, timeout, 5xx, circuito aberto → `ParticipanteIndisponivelError` (transitório).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol
from uuid import UUID

# Tipos de problema do principal (docs/05-api.md, seção 5.8) que a saga trata.
VERSAO_DIVERGENTE = "versao-divergente"
VEICULO_INDISPONIVEL = "veiculo-indisponivel"
VEICULO_NAO_ENCONTRADO = "veiculo-nao-encontrado"
VEICULO_JA_VENDIDO = "veiculo-ja-vendido"
BLOQUEIO_DE_OUTRA_COMPRA = "bloqueio-de-outra-compra"
COMPRA_COMPENSADA = "compra-compensada"
COBRANCA_JA_PAGA = "cobranca-ja-paga"


class ParticipanteIndisponivelError(Exception):
    """Falha transitória: o passo pode ou não ter acontecido; repetir é seguro."""

    def __init__(self, destino: str, detalhe: str) -> None:
        self.destino = destino
        self.detalhe = detalhe
        super().__init__(f"{destino} indisponível: {detalhe}")


class RecusaDoParticipanteError(Exception):
    """O participante respondeu e recusou o passo por regra de negócio."""

    def __init__(self, tipo: str, status: int) -> None:
        self.tipo = tipo
        self.status = status
        super().__init__(f"recusado pelo participante: {tipo} (HTTP {status})")


class CatalogoPort(Protocol):
    def bloquear(self, veiculo_id: UUID, compra_id: UUID, versao: int) -> None: ...

    def desbloquear(self, veiculo_id: UUID, compra_id: UUID) -> None: ...

    def vender(self, veiculo_id: UUID, compra_id: UUID) -> None: ...


class PagamentosPort(Protocol):
    def gerar_cobranca(self, compra_id: UUID, valor: Decimal) -> str:
        """Devolve o código do pagamento."""
        ...

    def cancelar_cobranca(self, compra_id: UUID) -> None: ...


class Cifrador(Protocol):
    def cifrar(self, texto: str, associado: str) -> str: ...

    def decifrar(self, cifrado: str, associado: str) -> str: ...
