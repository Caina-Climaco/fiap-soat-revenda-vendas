"""Casos de uso de Vendas: vitrine, compra, resultado do pagamento e recuperação."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from vendas.application.portas import Cifrador
from vendas.application.saga import OrquestradorCompra
from vendas.domain.anuncio import Anuncio, DadosAnuncio, StatusAnuncio
from vendas.domain.compra import Compra, MotivoCancelamento, Passo, ResultadoPagamento
from vendas.domain.cpf import Cpf
from vendas.domain.erros import (
    AnuncioAlteradoError,
    AnuncioNaoEncontradoError,
    CompraNaoEncontradaError,
    ServicoPrincipalIndisponivelError,
    VeiculoIndisponivelError,
)
from vendas.domain.repositorio import (
    AnuncioRepository,
    CompraAtivaExistenteError,
    CompraRepository,
    ConflitoRevisaoError,
)
from vendas.shared.clock import Clock
from vendas.shared.eventos import PublicadorEventos
from vendas.shared.paginacao import Pagina


class AtualizarAnuncio:
    """Réplica do anúncio enviada pela outbox do principal (ADR-003 do serviço de venda)."""

    def __init__(self, anuncios: AnuncioRepository, relogio: Clock) -> None:
        self._anuncios = anuncios
        self._relogio = relogio

    def executar(self, dados: DadosAnuncio) -> bool:
        return self._anuncios.aplicar_replica(dados, self._relogio.agora())


class ObterAnuncio:
    def __init__(self, anuncios: AnuncioRepository) -> None:
        self._anuncios = anuncios

    def executar(self, veiculo_id: UUID) -> Anuncio:
        anuncio = self._anuncios.obter(veiculo_id)
        if anuncio is None:
            raise AnuncioNaoEncontradoError(veiculo_id)
        return anuncio


class ListarVitrine:
    """Veículos à venda ou vendidos, do mais barato ao mais caro (RF-03, RF-04)."""

    def __init__(self, anuncios: AnuncioRepository, status: StatusAnuncio) -> None:
        self._anuncios = anuncios
        self._status = status

    def executar(self, *, limite: int, deslocamento: int) -> Pagina[Anuncio]:
        itens, total = self._anuncios.listar(self._status, limite=limite, deslocamento=deslocamento)
        return Pagina(itens=itens, total=total, limite=limite, deslocamento=deslocamento)


class IniciarCompra:
    """RF-05: cria a compra e roda a parte síncrona da saga (T1, T2, T3)."""

    def __init__(
        self,
        anuncios: AnuncioRepository,
        compras: CompraRepository,
        orquestrador: OrquestradorCompra,
        cifrador: Cifrador,
        relogio: Clock,
        publicador: PublicadorEventos,
        *,
        prazo_reserva: timedelta,
    ) -> None:
        self._anuncios = anuncios
        self._compras = compras
        self._orquestrador = orquestrador
        self._cifrador = cifrador
        self._relogio = relogio
        self._publicador = publicador
        self._prazo = prazo_reserva

    def executar(self, veiculo_id: UUID, versao: int, cpf_texto: str) -> Compra:
        cpf = Cpf.de(cpf_texto)
        anuncio = self._anuncio_disponivel(veiculo_id, versao)
        agora = self._relogio.agora()
        compra_id = uuid.uuid4()
        compra = Compra.iniciar(
            id_=compra_id,
            veiculo_id=veiculo_id,
            versao_anuncio=versao,
            marca=anuncio.marca,
            modelo=anuncio.modelo,
            ano=anuncio.ano,
            cor=anuncio.cor,
            preco=anuncio.preco,
            cpf=cpf,
            # O id da compra entra como dado associado: a cifra de uma compra não serve
            # em outra (ADR-005 do serviço de venda).
            cpf_cifrado=self._cifrador.cifrar(cpf.digitos, str(compra_id)),
            agora=agora,
            prazo=self._prazo,
        )
        # Gravada já com T1 iniciado e com o lease desta requisição: se o processo cair
        # aqui, a recuperação sabe que precisa liberar o anúncio.
        compra.iniciar_passo(Passo.RESERVA, agora)
        compra.saga.lease_ate = agora + self._orquestrador.duracao_lease
        compra.saga.dono_lease = self._orquestrador.dono
        try:
            self._compras.inserir(compra)
        except CompraAtivaExistenteError as exc:
            raise VeiculoIndisponivelError(veiculo_id) from exc
        self._publicador.publicar(compra.coletar_eventos())
        try:
            compra = self._orquestrador.avancar(compra)
        finally:
            self._compras.liberar_lease(compra.id, self._orquestrador.dono)
        self._traduzir_cancelamento(compra)
        return compra

    def _anuncio_disponivel(self, veiculo_id: UUID, versao: int) -> Anuncio:
        anuncio = self._anuncios.obter(veiculo_id)
        if anuncio is None:
            raise AnuncioNaoEncontradoError(veiculo_id)
        if anuncio.versao != versao:
            raise AnuncioAlteradoError(veiculo_id)
        if anuncio.status is StatusAnuncio.RESERVADO and anuncio.compra_id is not None:
            # Expiração imediata no caminho da compra (ADR-006): se a reserva anterior já
            # venceu, esta requisição a encerra antes de tentar reservar.
            anterior = self._compras.obter(anuncio.compra_id)
            if anterior is not None and anterior.expirada(self._relogio.agora()):
                self._orquestrador.processar(anterior.id)
                anuncio = self._anuncios.obter(veiculo_id) or anuncio
        if anuncio.status is not StatusAnuncio.A_VENDA:
            raise VeiculoIndisponivelError(veiculo_id)
        return anuncio

    @staticmethod
    def _traduzir_cancelamento(compra: Compra) -> None:
        match compra.motivo_cancelamento:
            case MotivoCancelamento.ANUNCIO_ALTERADO:
                raise AnuncioAlteradoError(compra.veiculo_id)
            case MotivoCancelamento.VEICULO_INDISPONIVEL:
                raise VeiculoIndisponivelError(compra.veiculo_id)
            case MotivoCancelamento.FALHA_PROCESSAMENTO:
                raise ServicoPrincipalIndisponivelError(compra.id)
            case _:
                return


class ObterCompra:
    def __init__(self, compras: CompraRepository) -> None:
        self._compras = compras

    def executar(self, compra_id: UUID) -> Compra:
        compra = self._compras.obter(compra_id)
        if compra is None:
            raise CompraNaoEncontradaError(compra_id)
        return compra


class RegistrarResultadoPagamento:
    """Repasse do principal (outbox): grava o resultado e tenta avançar a saga na hora."""

    def __init__(
        self, compras: CompraRepository, orquestrador: OrquestradorCompra, relogio: Clock
    ) -> None:
        self._compras = compras
        self._orquestrador = orquestrador
        self._relogio = relogio

    def executar(self, compra_id: UUID, codigo: str, resultado: ResultadoPagamento) -> Compra:
        for _ in range(3):  # releitura em caso de corrida com outro repasse
            compra = self._compras.obter(compra_id)
            if compra is None:
                # 404: a outbox do principal tenta de novo mais tarde.
                raise CompraNaoEncontradaError(compra_id)
            agora = self._relogio.agora()
            if not compra.registrar_resultado(resultado, codigo, agora):
                break  # repetição: nada a gravar
            if self._compras.registrar_resultado(compra_id, resultado, codigo, agora):
                break
        # Se outro processo estiver com a saga, ele (ou a recuperação) fará o resto.
        processada = self._orquestrador.processar(compra_id)
        return processada or self._obter(compra_id)

    def _obter(self, compra_id: UUID) -> Compra:
        compra = self._compras.obter(compra_id)
        if compra is None:  # pragma: no cover - acabou de ser lida
            raise CompraNaoEncontradaError(compra_id)
        return compra


@dataclass(frozen=True, slots=True)
class ResultadoRecuperacao:
    sagas_processadas: int
    cpfs_apagados: int


class RecuperarSagas:
    """CronJob (ADR-006): expiração, retomada após queda, retentativas e retenção do CPF."""

    def __init__(
        self,
        compras: CompraRepository,
        orquestrador: OrquestradorCompra,
        relogio: Clock,
        *,
        retencao_cpf: timedelta,
    ) -> None:
        self._compras = compras
        self._orquestrador = orquestrador
        self._relogio = relogio
        self._retencao = retencao_cpf

    def executar(self, limite: int = 500) -> ResultadoRecuperacao:
        agora = self._relogio.agora()
        processadas = sum(
            self._orquestrador.processar(compra_id) is not None
            for compra_id in self._compras.pendentes(agora, limite)
        )
        apagados = 0
        for compra_id in self._compras.canceladas_com_cpf_ate(agora - self._retencao, limite):
            compra = self._compras.obter(compra_id)
            if compra is None:  # pragma: no cover - compras nunca são apagadas
                continue
            compra.apagar_cpf(agora)
            try:
                self._compras.salvar(compra)
            except ConflitoRevisaoError:  # pragma: no cover - fica para a próxima execução
                continue
            apagados += 1
        return ResultadoRecuperacao(processadas, apagados)


@dataclass(frozen=True, slots=True)
class CasosUsoVendas:
    atualizar_anuncio: AtualizarAnuncio
    obter_anuncio: ObterAnuncio
    listar_a_venda: ListarVitrine
    listar_vendidos: ListarVitrine
    iniciar_compra: IniciarCompra
    obter_compra: ObterCompra
    registrar_resultado: RegistrarResultadoPagamento


FabricaCasosUsoVendas = Callable[..., CasosUsoVendas]
