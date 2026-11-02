"""Composição do serviço de venda: liga repositórios, participantes, cifra e saga.

O cliente do MongoDB e o cliente HTTP do principal são thread-safe e reaproveitados entre
requisições; os casos de uso não guardam estado de requisição, então são montados uma vez.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import timedelta

from vendas.application.casos_uso import (
    AtualizarAnuncio,
    CasosUsoVendas,
    IniciarCompra,
    ListarVitrine,
    ObterAnuncio,
    ObterCompra,
    RecuperarSagas,
    RegistrarResultadoPagamento,
)
from vendas.application.saga import OrquestradorCompra
from vendas.domain.anuncio import StatusAnuncio
from vendas.domain.compra import MotivoCancelamento
from vendas.domain.eventos import (
    CompraAguardandoPagamento,
    CompraCancelada,
    CompraEfetivada,
    CompraIniciada,
    PassoSagaExecutado,
)
from vendas.infrastructure.cifrador import CifradorAesGcm
from vendas.infrastructure.participantes import CatalogoHttp, PagamentosHttp
from vendas.infrastructure.repositorio_mongo import (
    MongoAnuncioRepository,
    MongoCompraRepository,
)
from vendas.infrastructure.resiliencia import ClienteResiliente
from vendas.shared.clock import Clock
from vendas.shared.config import Settings
from vendas.shared.eventos import EventoDominio, PublicadorEventos
from vendas.shared.metricas import AcaoMetrica, Metricas, rotulo_do_evento
from vendas.shared.mongo import BancoMongo


def identificador_do_processo() -> str:
    """Prefixo do dono do lease: o nome do pod (hostname) e o pid; cada execução da saga
    acrescenta um sufixo único (OrquestradorCompra.novo_dono)."""
    return f"{socket.gethostname()}-{os.getpid()}"


@dataclass(frozen=True, slots=True)
class Componentes:
    casos_uso: CasosUsoVendas
    orquestrador: OrquestradorCompra
    recuperar: RecuperarSagas


def montar(
    settings: Settings,
    banco: BancoMongo,
    cliente_principal: ClienteResiliente,
    relogio: Clock,
    publicador: PublicadorEventos,
) -> Componentes:
    anuncios = MongoAnuncioRepository(banco.db)
    compras = MongoCompraRepository(banco.db)
    orquestrador = OrquestradorCompra(
        compras,
        anuncios,
        CatalogoHttp(cliente_principal),
        PagamentosHttp(cliente_principal),
        relogio,
        publicador,
        dono=identificador_do_processo(),
    )
    cifrador = CifradorAesGcm(
        {settings.cpf_chave_id: settings.chave_cpf_bytes}, settings.cpf_chave_id
    )
    casos_uso = CasosUsoVendas(
        atualizar_anuncio=AtualizarAnuncio(anuncios, relogio),
        obter_anuncio=ObterAnuncio(anuncios),
        listar_a_venda=ListarVitrine(anuncios, StatusAnuncio.A_VENDA),
        listar_vendidos=ListarVitrine(anuncios, StatusAnuncio.VENDIDO),
        iniciar_compra=IniciarCompra(
            anuncios,
            compras,
            orquestrador,
            cifrador,
            relogio,
            publicador,
            prazo_reserva=timedelta(minutes=settings.prazo_reserva_minutos),
        ),
        obter_compra=ObterCompra(compras),
        registrar_resultado=RegistrarResultadoPagamento(compras, orquestrador, relogio),
    )
    recuperar = RecuperarSagas(
        compras, orquestrador, relogio, retencao_cpf=timedelta(days=settings.retencao_cpf_dias)
    )
    return Componentes(casos_uso, orquestrador, recuperar)


def acoes_metricas_negocio(metricas: Metricas) -> dict[str, AcaoMetrica]:
    """Liga os eventos de domínio aos contadores de negócio."""
    for motivo in MotivoCancelamento:  # séries com zero desde o início (rate() sem lacunas)
        metricas.compras_canceladas.labels(motivo=motivo.value)

    def cancelada(evento: EventoDominio) -> None:
        metricas.compras_canceladas.labels(motivo=rotulo_do_evento(evento, "motivo")).inc()

    def passo(evento: EventoDominio) -> None:
        metricas.passos_saga.labels(
            passo=rotulo_do_evento(evento, "passo"),
            resultado=rotulo_do_evento(evento, "resultado"),
        ).inc()

    return {
        CompraIniciada.NOME: lambda _evento: metricas.compras_iniciadas.inc(),
        CompraAguardandoPagamento.NOME: lambda _evento: metricas.compras_aguardando.inc(),
        CompraEfetivada.NOME: lambda _evento: metricas.compras_efetivadas.inc(),
        CompraCancelada.NOME: cancelada,
        PassoSagaExecutado.NOME: passo,
    }
