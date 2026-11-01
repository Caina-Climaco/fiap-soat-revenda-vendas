"""Contratos das rotas internas (ADR-008 da solucao), com a aplicacao real.

- Consumidor: toda chamada da saga ao principal (bloqueio, desbloqueio, venda, cobranca e
  cancelamento) segue a copia do contrato dele (contratos/principal-interno.openapi.json),
  e o principal falso, usado em todos os testes da saga, so responde o que o contrato
  permite. Assim o falso nao pode divergir do principal real sem quebrar este teste.
- Provedor: as respostas reais das rotas internas deste servico (replica do anuncio e
  resultado do pagamento) seguem o contrato publicado (contratos/vendas-interno.openapi.json).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from apoio.api import Api
from apoio.principal_falso import PrincipalFalso
from contrato.openapi_contrato import Contrato, carregar

pytestmark = pytest.mark.contrato

PRINCIPAL = Contrato(carregar("principal-interno.openapi.json"))
PROPRIO = Contrato(carregar("vendas-interno.openapi.json"))


class PrincipalFalsoConferido(PrincipalFalso):
    """O principal falso, com cada requisicao e resposta conferida contra o contrato."""

    conferidas: list[str]

    def __call__(self, requisicao: httpx.Request) -> httpx.Response:
        corpo: Any = json.loads(requisicao.content) if requisicao.content else None
        operacao = PRINCIPAL.validar_requisicao(requisicao.method, requisicao.url.path, corpo)
        resposta = super().__call__(requisicao)
        # 5xx simulam falha de infraestrutura (fora do contrato de proposito)
        if resposta.status_code < 500:
            PRINCIPAL.validar_resposta(
                requisicao.method, requisicao.url.path, resposta.status_code, resposta.json()
            )
        self.conferidas.append(f"{operacao.metodo} {operacao.modelo}")
        return resposta


@pytest.fixture
def principal() -> PrincipalFalso:
    falso = PrincipalFalsoConferido()
    falso.conferidas = []
    return falso


def _conferir_proprio(resposta: httpx.Response) -> httpx.Response:
    requisicao = resposta.request
    corpo: Any = json.loads(requisicao.content) if requisicao.content else None
    PROPRIO.validar_requisicao(requisicao.method, requisicao.url.path, corpo)
    PROPRIO.validar_resposta(
        requisicao.method, requisicao.url.path, resposta.status_code, resposta.json()
    )
    return resposta


def test_consumidor_todas_as_chamadas_da_saga(api: Api, principal: PrincipalFalso) -> None:
    assert isinstance(principal, PrincipalFalsoConferido)
    # Compra paga: T2, T3 e T6
    pago = api.replicar()
    compra = api.compra_ok(pago)
    _conferir_proprio(api.resultado(compra["id"], principal.pagar(compra["id"]), "PAGO"))
    # Pagamento cancelado: C3 e C2
    cancelado = api.replicar()
    outra = api.compra_ok(cancelado)
    cobranca = principal.cobrancas[outra["id"]]
    cobranca.status = "CANCELADA"
    _conferir_proprio(api.resultado(outra["id"], cobranca.codigo, "CANCELADO"))
    # Recusa do principal no T2 (versao divergente, 409 com o tipo do problema)
    alterado = api.replicar()
    principal.veiculo(alterado).versao = 9
    assert api.comprar(alterado).status_code == 409

    assert set(principal.conferidas) == {
        "POST /internal/v1/veiculos/{veiculo_id}/bloqueio",
        "DELETE /internal/v1/veiculos/{veiculo_id}/bloqueio/{compra_id}",
        "POST /internal/v1/veiculos/{veiculo_id}/venda",
        "POST /internal/v1/cobrancas",
        "POST /internal/v1/cobrancas/{compra_id}/cancelamento",
    }


def test_provedor_replica_e_resultado(api: Api, principal: PrincipalFalso) -> None:
    veiculo = str(uuid.uuid4())
    corpo = {
        "versao": 1,
        "marca": "Fiat",
        "modelo": "Argo",
        "ano": 2022,
        "cor": "Prata",
        "preco": "72900.00",
        "criado_em": "2026-11-01T10:00:00Z",
    }
    caminho = f"/internal/v1/anuncios/{veiculo}"
    _conferir_proprio(api.http.put(caminho, json=corpo, headers={"X-Service-Token": "x" * 16}))
    from apoio.api import SERVICO

    _conferir_proprio(api.http.put(caminho, json=corpo, headers=SERVICO))
    _conferir_proprio(api.http.put(caminho, json=corpo, headers=SERVICO))  # repetida
    compra = api.compra_ok(veiculo)
    codigo = principal.pagar(compra["id"])
    _conferir_proprio(api.resultado(compra["id"], codigo, "PAGO"))
    _conferir_proprio(api.resultado(compra["id"], "PAG-ffffffffffff", "PAGO"))  # divergente
    _conferir_proprio(api.resultado(str(uuid.uuid4()), codigo, "PAGO"))  # desconhecida
