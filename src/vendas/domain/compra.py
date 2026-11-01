"""Agregado Compra, com o estado da saga que a coordena (docs/02 da solução, 2.6.4 e 2.8).

A Compra é a "venda" do enunciado: CPF do comprador, preço congelado e data da venda. O
`EstadoSaga` guarda o progresso técnico do processo: passos iniciados e concluídos,
compensações pendentes, retentativas e o lease de quem está executando. Os dois são
gravados no mesmo documento, então cada mudança de estado é atômica.

Regras de decisão (ADR-002 e ADR-006 do serviço de venda):
- Falha antes de o comprador receber o código do pagamento: a compra é cancelada na hora e
  as compensações dos passos iniciados ficam pendentes até concluírem.
- Expiração na espera do pagamento: a cobrança é cancelada primeiro (C3, o árbitro); só
  depois a compra é cancelada. Se a cobrança já estava paga, a compra segue em frente.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from vendas.domain.cpf import Cpf
from vendas.domain.erros import ResultadoDivergenteError
from vendas.domain.eventos import (
    CompraAguardandoPagamento,
    CompraCancelada,
    CompraEfetivada,
    CompraIniciada,
    EventoVendas,
    PassoSagaExecutado,
)


class StatusCompra(StrEnum):
    PROCESSANDO = "PROCESSANDO"
    AGUARDANDO_PAGAMENTO = "AGUARDANDO_PAGAMENTO"
    EFETIVADA = "EFETIVADA"
    CANCELADA = "CANCELADA"


class MotivoCancelamento(StrEnum):
    PAGAMENTO_CANCELADO = "PAGAMENTO_CANCELADO"
    RESERVA_EXPIRADA = "RESERVA_EXPIRADA"
    ANUNCIO_ALTERADO = "ANUNCIO_ALTERADO"
    VEICULO_INDISPONIVEL = "VEICULO_INDISPONIVEL"
    FALHA_PROCESSAMENTO = "FALHA_PROCESSAMENTO"


class ResultadoPagamento(StrEnum):
    PAGO = "PAGO"
    CANCELADO = "CANCELADO"


class Passo(StrEnum):
    RESERVA = "T1_RESERVA"
    BLOQUEIO = "T2_BLOQUEIO"
    COBRANCA = "T3_COBRANCA"
    EFETIVACAO = "T5_EFETIVACAO"
    VENDA_CATALOGO = "T6_VENDA_CATALOGO"


class Compensacao(StrEnum):
    LIBERACAO = "C1_LIBERACAO"
    DESBLOQUEIO = "C2_DESBLOQUEIO"
    CANCELAMENTO_COBRANCA = "C3_CANCELAMENTO_COBRANCA"


PASSOS_ANTES_DO_PIVO = (Passo.RESERVA, Passo.BLOQUEIO, Passo.COBRANCA)
COMPENSACAO_DE = {
    Passo.RESERVA: Compensacao.LIBERACAO,
    Passo.BLOQUEIO: Compensacao.DESBLOQUEIO,
    Passo.COBRANCA: Compensacao.CANCELAMENTO_COBRANCA,
}
ATIVAS = frozenset(
    {StatusCompra.PROCESSANDO, StatusCompra.AGUARDANDO_PAGAMENTO, StatusCompra.EFETIVADA}
)
_RETENTATIVA_MAXIMA = timedelta(minutes=5)


@dataclass
class EstadoSaga:
    iniciados: list[Passo] = field(default_factory=list)
    concluidos: list[Passo] = field(default_factory=list)
    compensacoes_pendentes: list[Compensacao] = field(default_factory=list)
    compensacoes_feitas: list[Compensacao] = field(default_factory=list)
    tentativas: int = 0
    proxima_tentativa: datetime | None = None
    ultimo_erro: str | None = None
    lease_ate: datetime | None = None
    dono_lease: str | None = None


@dataclass(eq=False)
class Compra:
    id: UUID
    veiculo_id: UUID
    versao_anuncio: int
    marca: str
    modelo: str
    ano: int
    cor: str
    preco: Decimal
    cpf_cifrado: str | None
    cpf_mascarado: str | None
    status: StatusCompra
    criada_em: datetime
    expira_em: datetime
    atualizada_em: datetime
    motivo_cancelamento: MotivoCancelamento | None = None
    codigo_pagamento: str | None = None
    resultado_pagamento: ResultadoPagamento | None = None
    data_venda: datetime | None = None
    saga: EstadoSaga = field(default_factory=EstadoSaga)
    revisao: int = 0
    _eventos: list[EventoVendas] = field(default_factory=list, repr=False)

    @classmethod
    def iniciar(
        cls,
        *,
        veiculo_id: UUID,
        versao_anuncio: int,
        marca: str,
        modelo: str,
        ano: int,
        cor: str,
        preco: Decimal,
        cpf: Cpf,
        cpf_cifrado: str,
        agora: datetime,
        prazo: timedelta,
        id_: UUID | None = None,
    ) -> Compra:
        """Compra em processamento, com preço e descrição congelados (RN-01)."""
        compra = cls(
            id=id_ or uuid.uuid4(),
            veiculo_id=veiculo_id,
            versao_anuncio=versao_anuncio,
            marca=marca,
            modelo=modelo,
            ano=ano,
            cor=cor,
            preco=preco,
            cpf_cifrado=cpf_cifrado,
            cpf_mascarado=cpf.mascarado,
            status=StatusCompra.PROCESSANDO,
            criada_em=agora,
            expira_em=agora + prazo,
            atualizada_em=agora,
        )
        compra._evento(CompraIniciada, agora)
        return compra

    # ------------------------------------------------------------ consultas

    @property
    def ativa(self) -> bool:
        """Conta para a regra de uma compra ativa por veículo (RN-03)."""
        return self.status in ATIVAS

    def concluido(self, passo: Passo) -> bool:
        return passo in self.saga.concluidos

    def expirada(self, agora: datetime) -> bool:
        return self.status is StatusCompra.AGUARDANDO_PAGAMENTO and agora >= self.expira_em

    def pode_tentar(self, agora: datetime) -> bool:
        proxima = self.saga.proxima_tentativa
        return proxima is None or agora >= proxima

    # ------------------------------------------------------------ passos da saga

    def iniciar_passo(self, passo: Passo, agora: datetime) -> None:
        """Marca o passo como iniciado antes da chamada: se o processo cair no meio, a
        compensação sabe que ele pode ter acontecido."""
        if passo not in self.saga.iniciados:
            self.saga.iniciados.append(passo)
        self.atualizada_em = agora

    def concluir_passo(self, passo: Passo, agora: datetime) -> None:
        self.iniciar_passo(passo, agora)
        if passo not in self.saga.concluidos:
            self.saga.concluidos.append(passo)
            self._evento(PassoSagaExecutado, agora, passo=passo.value, resultado="CONCLUIDO")
        self._limpar_retentativa()

    def registrar_cobranca(self, codigo: str, agora: datetime) -> None:
        """T3 concluído: o comprador recebe o código e a compra passa a esperar o pagamento."""
        self.codigo_pagamento = codigo
        self.concluir_passo(Passo.COBRANCA, agora)
        self.status = StatusCompra.AGUARDANDO_PAGAMENTO
        self._evento(CompraAguardandoPagamento, agora)

    def registrar_resultado(
        self, resultado: ResultadoPagamento, codigo: str, agora: datetime
    ) -> bool:
        """Resultado repassado pelo principal. False se for repetição do mesmo resultado."""
        if self.codigo_pagamento is not None and self.codigo_pagamento != codigo:
            raise ResultadoDivergenteError(self.id)
        if self.resultado_pagamento is resultado:
            return False
        if self.resultado_pagamento is not None:
            raise ResultadoDivergenteError(self.id)
        self.resultado_pagamento = resultado
        self.codigo_pagamento = codigo
        self.atualizada_em = agora
        return True

    def efetivar(self, agora: datetime) -> None:
        """T5: depois do pivô. Registra a data da venda (RN-07)."""
        self.status = StatusCompra.EFETIVADA
        self.data_venda = agora
        self.concluir_passo(Passo.EFETIVACAO, agora)
        self._evento(CompraEfetivada, agora)

    def cancelar(
        self,
        motivo: MotivoCancelamento,
        agora: datetime,
        *,
        ja_feitas: frozenset[Compensacao] = frozenset(),
    ) -> None:
        """Encerra a compra e agenda a compensação, em ordem inversa, de todo passo iniciado."""
        self.status = StatusCompra.CANCELADA
        self.motivo_cancelamento = motivo
        self.atualizada_em = agora
        for compensacao in ja_feitas:
            self._registrar_compensacao(compensacao, agora)
        self.saga.compensacoes_pendentes = [
            COMPENSACAO_DE[passo]
            for passo in reversed(self.saga.iniciados)
            if passo in COMPENSACAO_DE and COMPENSACAO_DE[passo] not in ja_feitas
        ]
        self._limpar_retentativa()
        self._evento(CompraCancelada, agora, motivo=motivo.value)

    def concluir_compensacao(self, compensacao: Compensacao, agora: datetime) -> None:
        if compensacao in self.saga.compensacoes_pendentes:
            self.saga.compensacoes_pendentes.remove(compensacao)
        self._registrar_compensacao(compensacao, agora)
        self.atualizada_em = agora

    def agendar_retentativa(self, erro: str, agora: datetime) -> None:
        """Falha transitória de um participante: tenta de novo com backoff (até 5 min)."""
        self.saga.tentativas += 1
        atraso = min(timedelta(seconds=2**self.saga.tentativas), _RETENTATIVA_MAXIMA)
        self.saga.proxima_tentativa = agora + atraso
        self.saga.ultimo_erro = erro[:300]
        self.atualizada_em = agora

    def apagar_cpf(self, agora: datetime) -> None:
        """Retenção (ADR-005): compra cancelada não guarda o CPF além do prazo."""
        self.cpf_cifrado = None
        self.cpf_mascarado = None
        self.atualizada_em = agora

    def anotar(self, passo: str, resultado: str, agora: datetime) -> None:
        """Registra um desfecho que não muda o estado (falha transitória, recusa, anomalia)."""
        self._evento(PassoSagaExecutado, agora, passo=passo, resultado=resultado)

    # ------------------------------------------------------------ eventos

    def coletar_eventos(self) -> list[EventoVendas]:
        eventos, self._eventos = self._eventos, []
        return eventos

    def _registrar_compensacao(self, compensacao: Compensacao, agora: datetime) -> None:
        if compensacao not in self.saga.compensacoes_feitas:
            self.saga.compensacoes_feitas.append(compensacao)
            self._evento(PassoSagaExecutado, agora, passo=compensacao.value, resultado="COMPENSADO")

    def _limpar_retentativa(self) -> None:
        self.saga.tentativas = 0
        self.saga.proxima_tentativa = None
        self.saga.ultimo_erro = None

    def _evento(self, tipo: type[EventoVendas], agora: datetime, **extras: str) -> None:
        self._eventos.append(
            tipo(compra_id=self.id, veiculo_id=self.veiculo_id, ocorrido_em=agora, **extras)
        )
