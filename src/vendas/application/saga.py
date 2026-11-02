"""Orquestrador da saga da compra (ADR-002 do serviço de venda; docs/02 da solução, 2.8).

O orquestrador é um *process manager*: lê o estado da compra, decide a próxima ação,
executa e grava. Toda ação é idempotente, então repetir uma ação depois de uma queda (ou
de um conflito de revisão) é seguro. A mesma função `avancar` atende a requisição de
compra, o repasse do resultado do pagamento e a recuperação periódica.

| Estado da compra        | Próxima ação                                                |
|-------------------------|-------------------------------------------------------------|
| PROCESSANDO             | T1 reservar, T2 bloquear, T3 gerar cobrança (o primeiro que faltar) |
| AGUARDANDO_PAGAMENTO    | resultado PAGO: T5; CANCELADO ou prazo vencido: arbitrar (C3) |
| EFETIVADA               | T6 marcar vendido no catálogo, se faltar                    |
| CANCELADA               | compensações pendentes (C3, C2, C1)                         |
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import assert_never
from uuid import UUID, uuid4

from vendas.application.portas import (
    BLOQUEIO_DE_OUTRA_COMPRA,
    COBRANCA_JA_PAGA,
    VEICULO_JA_VENDIDO,
    VERSAO_DIVERGENTE,
    CatalogoPort,
    PagamentosPort,
    ParticipanteIndisponivelError,
    RecusaDoParticipanteError,
)
from vendas.domain.compra import (
    PASSOS_ANTES_DO_PIVO,
    Compensacao,
    Compra,
    MotivoCancelamento,
    Passo,
    ResultadoPagamento,
    StatusCompra,
)
from vendas.domain.repositorio import (
    AnuncioRepository,
    CompraRepository,
    ConflitoRevisaoError,
    ResultadoReserva,
)
from vendas.shared.clock import Clock
from vendas.shared.eventos import PublicadorEventos

_logger = logging.getLogger("vendas.saga")

# Teto de ações por chamada: a saga tem no máximo 6 passos e 3 compensações; o teto só
# existe para que um defeito nunca vire um laço infinito dentro de uma requisição.
_MAXIMO_DE_ACOES = 20

Acao = Callable[[Compra, datetime], None]

_MOTIVO_DA_RESERVA = {
    ResultadoReserva.NAO_ENCONTRADO: MotivoCancelamento.VEICULO_INDISPONIVEL,
    ResultadoReserva.VERSAO_DIVERGENTE: MotivoCancelamento.ANUNCIO_ALTERADO,
    ResultadoReserva.INDISPONIVEL: MotivoCancelamento.VEICULO_INDISPONIVEL,
}


class OrquestradorCompra:
    def __init__(
        self,
        compras: CompraRepository,
        anuncios: AnuncioRepository,
        catalogo: CatalogoPort,
        pagamentos: PagamentosPort,
        relogio: Clock,
        publicador: PublicadorEventos,
        *,
        dono: str,
        duracao_lease: timedelta = timedelta(seconds=30),
    ) -> None:
        self._compras = compras
        self._anuncios = anuncios
        self._catalogo = catalogo
        self._pagamentos = pagamentos
        self._relogio = relogio
        self._publicador = publicador
        self.dono = dono
        self.duracao_lease = duracao_lease

    # ------------------------------------------------------------ entrada

    def novo_dono(self) -> str:
        """Dono do lease de uma execução: o processo mais um sufixo único por chamada.

        Duas requisições no mesmo pod (threads diferentes) nunca dividem um lease, e uma
        execução que demorou além do lease não libera o lease de quem a substituiu.
        """
        return f"{self.dono}-{uuid4().hex[:12]}"

    def processar(self, compra_id: UUID) -> Compra | None:
        """Toma o lease, avança o que der e o devolve. None se outra execução está com ela."""
        dono = self.novo_dono()
        compra = self._compras.tomar_lease(
            compra_id, dono, self._relogio.agora(), self.duracao_lease
        )
        if compra is None:
            return None
        try:
            return self.avancar(compra)
        finally:
            self._compras.liberar_lease(compra_id, dono)

    def avancar(self, compra: Compra) -> Compra:
        """Executa ações até a saga esperar algo externo (pagamento ou nova tentativa)."""
        for _ in range(_MAXIMO_DE_ACOES):
            agora = self._relogio.agora()
            if not compra.pode_tentar(agora):
                break
            acao = self._proxima_acao(compra, agora)
            if acao is None:
                break
            agendada_antes = compra.saga.proxima_tentativa
            try:
                acao(compra, agora)
            except ConflitoRevisaoError:
                # O resultado do pagamento chegou no meio: relê e decide de novo.
                relida = self._compras.obter(compra.id)
                if relida is None:  # pragma: no cover - compras nunca são apagadas
                    break
                compra = relida
                continue
            agendada = compra.saga.proxima_tentativa
            if agendada is not None and agendada != agendada_antes:
                # A ação agendou uma nova tentativa (falha transitória ou compensações
                # adiadas): a saga espera (a recuperação retoma). Sem isso, como as chamadas
                # lentas consomem o tempo do backoff, a mesma requisição repetiria a
                # compensação remota.
                break
        return compra

    # ------------------------------------------------------------ decisão

    def _proxima_acao(self, compra: Compra, agora: datetime) -> Acao | None:
        match compra.status:
            case StatusCompra.PROCESSANDO:
                faltando = next(p for p in PASSOS_ANTES_DO_PIVO if not compra.concluido(p))
                return {
                    Passo.RESERVA: self._t1_reservar,
                    Passo.BLOQUEIO: self._t2_bloquear,
                    Passo.COBRANCA: self._t3_cobrar,
                }[faltando]
            case StatusCompra.AGUARDANDO_PAGAMENTO:
                if compra.resultado_pagamento is ResultadoPagamento.PAGO:
                    return self._t5_efetivar
                if compra.resultado_pagamento is ResultadoPagamento.CANCELADO:
                    return self._arbitrar(MotivoCancelamento.PAGAMENTO_CANCELADO)
                if compra.expirada(agora):
                    return self._arbitrar(MotivoCancelamento.RESERVA_EXPIRADA)
                return None
            case StatusCompra.EFETIVADA:
                return None if compra.concluido(Passo.VENDA_CATALOGO) else self._t6_vender
            case StatusCompra.CANCELADA:
                return self._compensar if compra.saga.compensacoes_pendentes else None
            case _:  # pragma: no cover - o mypy garante que todos os status foram tratados
                assert_never(compra.status)

    # ------------------------------------------------------------ passos antes do pivô

    def _t1_reservar(self, compra: Compra, agora: datetime) -> None:
        compra.iniciar_passo(Passo.RESERVA, agora)
        resultado = self._anuncios.reservar(
            compra.veiculo_id, compra.versao_anuncio, compra.id, agora
        )
        if resultado is ResultadoReserva.RESERVADO:
            compra.concluir_passo(Passo.RESERVA, agora)
        else:
            compra.cancelar(_MOTIVO_DA_RESERVA[resultado], agora)
        self._salvar(compra)

    def _t2_bloquear(self, compra: Compra, agora: datetime) -> None:
        self._remoto_antes_do_pivo(
            compra,
            agora,
            Passo.BLOQUEIO,
            lambda: self._catalogo.bloquear(compra.veiculo_id, compra.id, compra.versao_anuncio),
        )
        if compra.status is StatusCompra.PROCESSANDO:
            compra.concluir_passo(Passo.BLOQUEIO, agora)
            self._salvar(compra)

    def _t3_cobrar(self, compra: Compra, agora: datetime) -> None:
        codigos: list[str] = []
        self._remoto_antes_do_pivo(
            compra,
            agora,
            Passo.COBRANCA,
            lambda: codigos.append(self._pagamentos.gerar_cobranca(compra.id, compra.preco)),
        )
        if codigos:
            compra.registrar_cobranca(codigos[0], agora)
            self._salvar(compra)

    def _remoto_antes_do_pivo(
        self, compra: Compra, agora: datetime, passo: Passo, chamada: Callable[[], None]
    ) -> None:
        """Marca o passo, chama o participante e, se falhar, cancela e agenda compensações.

        Antes do pivô o comprador ainda não tem o código do pagamento, então nada impede
        desistir na hora: a compra é cancelada e o que foi iniciado é desfeito.
        """
        compra.iniciar_passo(passo, agora)
        self._salvar(compra)
        try:
            chamada()
        except RecusaDoParticipanteError as recusa:
            motivo = (
                MotivoCancelamento.ANUNCIO_ALTERADO
                if recusa.tipo == VERSAO_DIVERGENTE
                else MotivoCancelamento.VEICULO_INDISPONIVEL
                if passo is Passo.BLOQUEIO
                else MotivoCancelamento.FALHA_PROCESSAMENTO
            )
            compra.anotar(passo.value, f"RECUSADO:{recusa.tipo}", agora)
            compra.cancelar(motivo, agora)
            self._salvar(compra)
        except ParticipanteIndisponivelError as falha:
            _logger.warning(
                "participante indisponível antes do pivô; compra cancelada",
                extra={
                    "campos": {
                        "compra_id": str(compra.id),
                        "passo": passo.value,
                        "erro": falha.detalhe,
                    }
                },
            )
            compra.anotar(passo.value, "FALHA_TRANSITORIA", agora)
            compra.cancelar(MotivoCancelamento.FALHA_PROCESSAMENTO, agora)
            # O principal acabou de esgotar as tentativas: o anúncio volta à vitrine já (C1
            # é local) e as compensações remotas ficam com a recuperação, em vez de o
            # comprador esperar outra rodada de tentativas contra o mesmo principal (o passo
            # que falhou é T2 ou T3, então ao menos o C2 sempre fica pendente).
            self._executar_compensacao(Compensacao.LIBERACAO, compra, agora)
            compra.concluir_compensacao(Compensacao.LIBERACAO, agora)
            compra.adiar_compensacoes(f"{passo.value}: {falha.detalhe}", agora)
            self._salvar(compra)

    # ------------------------------------------------------------ espera do pagamento

    def _arbitrar(self, motivo: MotivoCancelamento) -> Acao:
        """Cancelamento na espera do pagamento: C3 primeiro, porque a cobrança é o árbitro."""

        def acao(compra: Compra, agora: datetime) -> None:
            try:
                self._pagamentos.cancelar_cobranca(compra.id)
            except RecusaDoParticipanteError as recusa:
                if recusa.tipo == COBRANCA_JA_PAGA:
                    # O pivô aconteceu antes: o pagamento venceu a expiração e a saga avança.
                    compra.anotar(Compensacao.CANCELAMENTO_COBRANCA.value, "PIVO_JA_OCORREU", agora)
                    compra.resultado_pagamento = ResultadoPagamento.PAGO
                    self._salvar(compra)
                    return
                # Qualquer outra recusa não impede o cancelamento: fica registrada.
                self._recusa_na_compensacao(
                    Compensacao.CANCELAMENTO_COBRANCA, compra, recusa, agora
                )
            except ParticipanteIndisponivelError as falha:
                compra.anotar(Compensacao.CANCELAMENTO_COBRANCA.value, "FALHA_TRANSITORIA", agora)
                compra.agendar_retentativa(falha.detalhe, agora)
                self._salvar(compra)
                return
            compra.cancelar(motivo, agora, ja_feitas=frozenset({Compensacao.CANCELAMENTO_COBRANCA}))
            self._salvar(compra)

        return acao

    # ------------------------------------------------------------ depois do pivô

    def _t5_efetivar(self, compra: Compra, agora: datetime) -> None:
        if not self._anuncios.vender(compra.veiculo_id, compra.id, compra.preco, agora):
            _logger.error(
                "anúncio não estava reservado pela compra paga",
                extra={"campos": {"compra_id": str(compra.id)}},
            )
            compra.anotar(Passo.EFETIVACAO.value, "ANOMALIA_ANUNCIO", agora)
        compra.efetivar(agora)
        self._salvar(compra)

    def _t6_vender(self, compra: Compra, agora: datetime) -> None:
        """Retentável: depois do pivô a saga só anda para a frente."""
        try:
            self._catalogo.vender(compra.veiculo_id, compra.id)
        except ParticipanteIndisponivelError as falha:
            compra.anotar(Passo.VENDA_CATALOGO.value, "FALHA_TRANSITORIA", agora)
            compra.agendar_retentativa(falha.detalhe, agora)
            self._salvar(compra)
            return
        except RecusaDoParticipanteError as recusa:
            # Inconsistência que exige análise humana; não adianta repetir.
            _logger.error(
                "catálogo recusou registrar a venda",
                extra={"campos": {"compra_id": str(compra.id), "tipo": recusa.tipo}},
            )
            compra.anotar(Passo.VENDA_CATALOGO.value, f"RECUSADO:{recusa.tipo}", agora)
        compra.concluir_passo(Passo.VENDA_CATALOGO, agora)
        self._salvar(compra)

    # ------------------------------------------------------------ compensações

    def _compensar(self, compra: Compra, agora: datetime) -> None:
        """Tenta todas as compensações pendentes; as remotas que falharem ficam para depois.

        A liberação do anúncio (C1, local) não espera as remotas: a regra contra
        ressurreição do principal garante que um passo atrasado desta compra nunca volte a
        bloquear o veículo, então devolver o anúncio à vitrine na hora é seguro.
        """
        falhas: list[str] = []
        for compensacao in list(compra.saga.compensacoes_pendentes):
            try:
                self._executar_compensacao(compensacao, compra, agora)
            except ParticipanteIndisponivelError as falha:
                compra.anotar(compensacao.value, "FALHA_TRANSITORIA", agora)
                falhas.append(f"{compensacao.value}: {falha.detalhe}")
                continue
            except RecusaDoParticipanteError as recusa:
                self._recusa_na_compensacao(compensacao, compra, recusa, agora)
            compra.concluir_compensacao(compensacao, agora)
        if falhas:
            compra.agendar_retentativa("; ".join(falhas), agora)
        self._salvar(compra)

    def _executar_compensacao(
        self, compensacao: Compensacao, compra: Compra, agora: datetime
    ) -> None:
        match compensacao:
            case Compensacao.LIBERACAO:
                self._anuncios.liberar(compra.veiculo_id, compra.id, agora)
            case Compensacao.DESBLOQUEIO:
                self._catalogo.desbloquear(compra.veiculo_id, compra.id)
            case Compensacao.CANCELAMENTO_COBRANCA:
                self._pagamentos.cancelar_cobranca(compra.id)
            case _:  # pragma: no cover - o mypy garante que todas foram tratadas
                assert_never(compensacao)

    def _recusa_na_compensacao(
        self,
        compensacao: Compensacao,
        compra: Compra,
        recusa: RecusaDoParticipanteError,
        agora: datetime,
    ) -> None:
        esperadas = {
            (Compensacao.DESBLOQUEIO, VEICULO_JA_VENDIDO),
            (Compensacao.DESBLOQUEIO, BLOQUEIO_DE_OUTRA_COMPRA),
            (Compensacao.CANCELAMENTO_COBRANCA, COBRANCA_JA_PAGA),
        }
        nivel = logging.ERROR if (compensacao, recusa.tipo) in esperadas else logging.WARNING
        _logger.log(
            nivel,
            "compensação recusada pelo participante; registrada para análise",
            extra={
                "campos": {
                    "compra_id": str(compra.id),
                    "compensacao": compensacao.value,
                    "tipo": recusa.tipo,
                }
            },
        )
        compra.anotar(compensacao.value, f"RECUSADO:{recusa.tipo}", agora)

    # ------------------------------------------------------------ persistência

    def _salvar(self, compra: Compra) -> None:
        self._compras.salvar(compra)
        self._publicador.publicar(compra.coletar_eventos())
