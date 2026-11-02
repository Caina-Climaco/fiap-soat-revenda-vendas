"""Cliente HTTP resiliente para o serviço principal (ADR-004 da solução).

- Timeout em toda chamada (padrão 2 s).
- Retry: até N tentativas, backoff exponencial com jitter, só em erro de rede, 502, 503 e 504.
- Circuit breaker: abre após K falhas seguidas; meio-aberto depois de T s (uma chamada de
  teste); com o circuito aberto, a chamada falha na hora, sem rede (fail fast).
- Bulkhead: pool de conexões limitado para o destino.

O retry só é seguro porque toda rota interna do principal é idempotente pela `compra_id`.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Mapping
from enum import IntEnum

import httpx

from vendas.application.portas import ParticipanteIndisponivelError
from vendas.shared.auth import CABECALHO_TOKEN_SERVICO
from vendas.shared.metricas import Metricas

_logger = logging.getLogger("vendas.resiliencia")

STATUS_REPETIVEIS = frozenset({502, 503, 504})


class EstadoCircuito(IntEnum):
    FECHADO = 0
    MEIO_ABERTO = 1
    ABERTO = 2


class CircuitBreaker:
    def __init__(
        self,
        nome: str,
        *,
        limite_falhas: int = 5,
        tempo_aberto_segundos: float = 15.0,
        monotonic: Callable[[], float] = time.monotonic,
        ao_mudar: Callable[[str, EstadoCircuito], None] | None = None,
    ) -> None:
        self.nome = nome
        self._limite = limite_falhas
        self._tempo_aberto = tempo_aberto_segundos
        self._monotonic = monotonic
        self._ao_mudar = ao_mudar
        self._trava = threading.Lock()
        self._estado = EstadoCircuito.FECHADO
        self._falhas = 0
        self._aberto_em = 0.0
        self._teste_em_andamento = False

    @property
    def estado(self) -> EstadoCircuito:
        with self._trava:
            return self._estado

    def permitir(self) -> bool:
        """False com o circuito aberto (fail fast). No meio-aberto, libera uma chamada."""
        with self._trava:
            if self._estado is EstadoCircuito.ABERTO:
                if self._monotonic() - self._aberto_em < self._tempo_aberto:
                    return False
                self._mudar(EstadoCircuito.MEIO_ABERTO)
            if self._estado is EstadoCircuito.MEIO_ABERTO:
                if self._teste_em_andamento:
                    return False
                self._teste_em_andamento = True
            return True

    def registrar_sucesso(self) -> None:
        with self._trava:
            self._falhas = 0
            self._teste_em_andamento = False
            if self._estado is not EstadoCircuito.FECHADO:
                self._mudar(EstadoCircuito.FECHADO)

    def registrar_falha(self) -> None:
        with self._trava:
            self._falhas += 1
            self._teste_em_andamento = False
            if self._estado is EstadoCircuito.MEIO_ABERTO or self._falhas >= self._limite:
                self._aberto_em = self._monotonic()
                if self._estado is not EstadoCircuito.ABERTO:
                    self._mudar(EstadoCircuito.ABERTO)

    def _mudar(self, estado: EstadoCircuito) -> None:
        self._estado = estado
        _logger.warning(
            "circuito %s: %s", self.nome, estado.name, extra={"campos": {"destino": self.nome}}
        )
        if self._ao_mudar is not None:
            self._ao_mudar(self.nome, estado)


class ClienteResiliente:
    def __init__(
        self,
        destino: str,
        url_base: str,
        token: str,
        *,
        timeout_segundos: float = 2.0,
        tentativas: int = 3,
        backoff_base_segundos: float = 0.1,
        circuito: CircuitBreaker | None = None,
        transporte: httpx.BaseTransport | None = None,
        dormir: Callable[[float], None] = time.sleep,
        metricas: Metricas | None = None,
    ) -> None:
        self.destino = destino
        self._tentativas = tentativas
        self._backoff = backoff_base_segundos
        self._dormir = dormir
        self._metricas = metricas
        self.circuito = circuito or CircuitBreaker(destino)
        self._http = httpx.Client(
            base_url=url_base,
            timeout=httpx.Timeout(timeout_segundos),
            headers={CABECALHO_TOKEN_SERVICO: token},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            transport=transporte,
        )

    def chamar(
        self, metodo: str, caminho: str, corpo: Mapping[str, object] | None = None
    ) -> httpx.Response:
        """Resposta do participante (2xx a 4xx), ou ParticipanteIndisponivelError."""
        if not self.circuito.permitir():
            self._contar("circuito_aberto")
            raise ParticipanteIndisponivelError(self.destino, "circuito aberto")
        ultimo_erro = ""
        try:
            for tentativa in range(1, self._tentativas + 1):
                try:
                    resposta = self._http.request(
                        metodo, caminho, json=None if corpo is None else dict(corpo)
                    )
                except httpx.TransportError as exc:
                    ultimo_erro = type(exc).__name__
                else:
                    codigo = resposta.status_code
                    if codigo < 500:
                        self.circuito.registrar_sucesso()
                        self._contar("sucesso" if codigo < 400 else "recusa")
                        return resposta
                    ultimo_erro = f"HTTP {codigo}"
                    if codigo not in STATUS_REPETIVEIS:
                        break  # erro do servidor que não adianta repetir agora
                if tentativa < self._tentativas:
                    self._contar("nova_tentativa")
                    # Jitter "cheio": espalha as novas tentativas de várias réplicas.
                    self._dormir(random.uniform(0, self._backoff * 2 ** (tentativa - 1)))  # noqa: S311
        except Exception:
            # Erro inesperado (ex.: httpx.InvalidURL): conta como falha; senão a chamada de
            # teste do meio-aberto nunca termina e o circuito fica preso recusando tudo.
            self.circuito.registrar_falha()
            self._contar("falha")
            raise
        self.circuito.registrar_falha()
        self._contar("falha")
        raise ParticipanteIndisponivelError(self.destino, ultimo_erro)

    def fechar(self) -> None:
        self._http.close()

    def _contar(self, resultado: str) -> None:
        if self._metricas is not None:
            self._metricas.chamadas_participante.labels(
                destino=self.destino, resultado=resultado
            ).inc()
