"""Circuit breaker, retry, tradução das respostas do principal e cifra do CPF."""

from __future__ import annotations

import base64
from collections.abc import Callable
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from cryptography.exceptions import InvalidTag

from vendas.application.portas import ParticipanteIndisponivelError, RecusaDoParticipanteError
from vendas.infrastructure.cifrador import ChaveDesconhecidaError, CifradorAesGcm
from vendas.infrastructure.participantes import CatalogoHttp, PagamentosHttp
from vendas.infrastructure.resiliencia import CircuitBreaker, ClienteResiliente, EstadoCircuito
from vendas.shared.metricas import Metricas


class Relogio:
    def __init__(self) -> None:
        self.agora = 0.0

    def __call__(self) -> float:
        return self.agora


def test_circuito_abre_meio_abre_e_fecha() -> None:
    relogio = Relogio()
    mudancas: list[EstadoCircuito] = []
    circuito = CircuitBreaker(
        "principal",
        limite_falhas=2,
        tempo_aberto_segundos=10,
        monotonic=relogio,
        ao_mudar=lambda _n, e: mudancas.append(e),
    )
    assert circuito.permitir()
    circuito.registrar_falha()
    assert circuito.estado is EstadoCircuito.FECHADO
    circuito.registrar_falha()
    assert circuito.estado is EstadoCircuito.ABERTO
    assert not circuito.permitir()
    relogio.agora = 11
    assert circuito.permitir()  # meio-aberto: uma chamada de teste
    assert not circuito.permitir()  # só uma por vez
    circuito.registrar_falha()  # o teste falhou: abre de novo
    assert circuito.estado is EstadoCircuito.ABERTO
    relogio.agora = 22
    assert circuito.permitir()
    circuito.registrar_sucesso()
    assert circuito.estado is EstadoCircuito.FECHADO
    assert mudancas == [
        EstadoCircuito.ABERTO,
        EstadoCircuito.MEIO_ABERTO,
        EstadoCircuito.ABERTO,
        EstadoCircuito.MEIO_ABERTO,
        EstadoCircuito.FECHADO,
    ]


def _cliente(
    responder: Callable[[httpx.Request], httpx.Response], **kwargs: object
) -> tuple[ClienteResiliente, list[float]]:
    esperas: list[float] = []
    cliente = ClienteResiliente(
        "principal",
        "http://principal",
        "token-0123456789abcdef",
        transporte=httpx.MockTransport(responder),
        dormir=esperas.append,
        **kwargs,  # type: ignore[arg-type]
    )
    return cliente, esperas


def test_retry_em_503_e_sucesso_na_terceira() -> None:
    respostas = iter([503, 502, 200])
    metricas = Metricas()
    cliente, esperas = _cliente(lambda _r: httpx.Response(next(respostas)), metricas=metricas)
    assert cliente.chamar("POST", "/x", {"a": 1}).status_code == 200
    assert len(esperas) == 2
    assert all(0 <= e <= 0.2 for e in esperas)
    texto = metricas.exportar().decode()
    assert 'resultado="nova_tentativa"} 2.0' in texto
    assert 'resultado="sucesso"} 1.0' in texto


def test_sem_retry_em_500_nem_em_4xx() -> None:
    chamadas: list[int] = []

    def responder(_r: httpx.Request) -> httpx.Response:
        chamadas.append(1)
        return httpx.Response(500)

    cliente, _ = _cliente(responder)
    with pytest.raises(ParticipanteIndisponivelError, match="HTTP 500"):
        cliente.chamar("POST", "/x")
    assert len(chamadas) == 1
    recusa, _ = _cliente(lambda _r: httpx.Response(409))
    assert recusa.chamar("POST", "/x").status_code == 409


def test_erro_de_rede_esgota_tentativas_e_conta_no_circuito() -> None:
    def falhar(requisicao: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("lento", request=requisicao)

    circuito = CircuitBreaker("principal", limite_falhas=1)
    cliente, esperas = _cliente(falhar, circuito=circuito, tentativas=3)
    with pytest.raises(ParticipanteIndisponivelError, match="ConnectTimeout"):
        cliente.chamar("GET", "/x")
    assert len(esperas) == 2
    with pytest.raises(ParticipanteIndisponivelError, match="circuito aberto"):
        cliente.chamar("GET", "/x")
    cliente.fechar()


def _problema(status: int, tipo: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": f"urn:revenda:problema:{tipo}"},
        headers={"content-type": "application/problem+json"},
    )


@pytest.mark.parametrize(
    ("resposta", "erro"),
    [
        (_problema(409, "versao-divergente"), RecusaDoParticipanteError),
        (_problema(404, "veiculo-nao-encontrado"), RecusaDoParticipanteError),
        (_problema(401, "servico-nao-autorizado"), ParticipanteIndisponivelError),
        (httpx.Response(422, json={}), ParticipanteIndisponivelError),
        (httpx.Response(409, text="sem problem+json"), ParticipanteIndisponivelError),
    ],
)
def test_traducao_das_respostas_do_catalogo(resposta: httpx.Response, erro: type) -> None:
    cliente, _ = _cliente(lambda _r: resposta)
    with pytest.raises(erro):
        CatalogoHttp(cliente).bloquear(uuid4(), uuid4(), 1)


def test_contrato_das_chamadas_ao_principal() -> None:
    recebidas: list[httpx.Request] = []

    def responder(requisicao: httpx.Request) -> httpx.Response:
        recebidas.append(requisicao)
        return httpx.Response(201, json={"codigo_pagamento": "PAG-000000000001"})

    cliente, _ = _cliente(responder)
    veiculo, compra = uuid4(), uuid4()
    catalogo, pagamentos = CatalogoHttp(cliente), PagamentosHttp(cliente)
    catalogo.bloquear(veiculo, compra, 3)
    catalogo.desbloquear(veiculo, compra)
    catalogo.vender(veiculo, compra)
    assert pagamentos.gerar_cobranca(compra, Decimal("72000")) == "PAG-000000000001"
    pagamentos.cancelar_cobranca(compra)
    assert [(r.method, r.url.path) for r in recebidas] == [
        ("POST", f"/internal/v1/veiculos/{veiculo}/bloqueio"),
        ("DELETE", f"/internal/v1/veiculos/{veiculo}/bloqueio/{compra}"),
        ("POST", f"/internal/v1/veiculos/{veiculo}/venda"),
        ("POST", "/internal/v1/cobrancas"),
        ("POST", f"/internal/v1/cobrancas/{compra}/cancelamento"),
    ]
    assert recebidas[0].headers["X-Service-Token"] == "token-0123456789abcdef"
    assert recebidas[3].content == (f'{{"compra_id":"{compra}","valor":"72000.00"}}').encode()


# ---------------------------------------------------------------- cifra


def test_cifra_aes_gcm_com_dado_associado_e_rotacao() -> None:
    k1, k2 = bytes(32), bytes(range(32))
    antigo = CifradorAesGcm({"k1": k1}, "k1")
    cifrado = antigo.cifrar("52998224725", "compra-a")
    assert cifrado.startswith("k1:")
    assert "52998224725" not in cifrado
    assert antigo.cifrar("52998224725", "compra-a") != cifrado  # nonce aleatório
    novo = CifradorAesGcm({"k1": k1, "k2": k2}, "k2")
    assert novo.decifrar(cifrado, "compra-a") == "52998224725"
    assert novo.cifrar("x", "y").startswith("k2:")
    with pytest.raises(InvalidTag):
        novo.decifrar(cifrado, "compra-b")  # a cifra de uma compra não serve em outra
    with pytest.raises(ChaveDesconhecidaError):
        antigo.decifrar(novo.cifrar("x", "y"), "y")
    with pytest.raises(ChaveDesconhecidaError):
        CifradorAesGcm({"k1": k1}, "k9")
    assert len(base64.b64decode(cifrado[3:])) == 12 + 11 + 16
