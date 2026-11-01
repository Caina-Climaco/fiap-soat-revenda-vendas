"""Recuperação de sagas no ambiente real (CronJob revenda-vendas-recuperacao).

A expiração da reserva (30 min) e a retenção do CPF (90 dias) não cabem no tempo de um CD.
O teste adianta o relógio do dado, não o do sistema: altera direto no MongoDB a data de
expiração ou a data da última atualização da compra e executa a recuperação, criando um Job
a partir do CronJob, exatamente como o Kubernetes faz a cada minuto.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from apoio_e2e import Ambiente, anuncio, compra, comprar, esperar, notificar

pytestmark = [pytest.mark.e2e, pytest.mark.caos]

NS = os.environ.get("E2E_NS_VENDAS", "vendas")
MONGOSH = (
    'mongosh -u "$MONGO_APP_USUARIO" -p "$MONGO_APP_SENHA" '
    '--authenticationDatabase "$MONGO_APP_BANCO" "$MONGO_APP_BANCO" --quiet --eval "$1"'
)


def _kubectl(*args: str) -> str:
    # kubectl do PATH do runner do CD, com argumentos montados por este módulo
    comando = ["kubectl", "-n", NS, *args]
    return subprocess.run(  # noqa: S603
        comando, check=True, capture_output=True, text=True, timeout=200
    ).stdout


def _mongo(script: str) -> str:
    """Executa um trecho de mongosh no pod do MongoDB, com o usuário da aplicação."""
    return _kubectl("exec", "statefulset/vendas-db", "--", "sh", "-c", MONGOSH, "sh", script)


def _executar_recuperacao() -> None:
    job = f"e2e-recuperacao-{uuid.uuid4().hex[:8]}"
    _kubectl("create", "job", job, "--from=cronjob/revenda-vendas-recuperacao")
    try:
        _kubectl("wait", "--for=condition=complete", "--timeout=120s", f"job/{job}")
    finally:
        _kubectl("delete", "job", job, "--ignore-not-found", "--wait=false")


@pytest.fixture
def aguardando(http: httpx.Client, novo_veiculo: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    veiculo = novo_veiculo()
    publicado = esperar(lambda: anuncio(http, veiculo["id"]), "anuncio na vitrine")
    resposta = comprar(http, veiculo["id"], publicado["versao"])
    assert resposta.status_code == 201, resposta.text
    corpo: dict[str, Any] = resposta.json()
    return corpo


def test_reserva_expirada_e_desfeita_pela_recuperacao(
    http: httpx.Client, ambiente: Ambiente, gestor: dict[str, str], aguardando: dict[str, Any]
) -> None:
    compra_id, veiculo_id = aguardando["id"], aguardando["veiculo_id"]
    _mongo(
        f'db.compras.updateOne({{_id: "{compra_id}"}}, '
        "{$set: {expira_em: new Date(Date.now() - 60000)}})"
    )
    _executar_recuperacao()

    expirada = compra(http, compra_id)
    assert expirada["status"] == "CANCELADA"
    assert expirada["motivo_cancelamento"] == "RESERVA_EXPIRADA"
    assert anuncio(http, veiculo_id)["status"] == "A_VENDA"
    # C2 e C3 no principal: o veículo volta a ser editável e a cobrança já está cancelada
    # (um cancelamento repetido do processador não tem mais efeito)
    edicao = http.patch(f"/api/v1/veiculos/{veiculo_id}", json={"cor": "Azul"}, headers=gestor)
    assert edicao.status_code == 200, edicao.text
    repetido = notificar(http, ambiente, aguardando["codigo_pagamento"], "CANCELADO")
    assert repetido.status_code == 200
    assert repetido.json()["status_cobranca"] == "CANCELADA"
    assert repetido.json()["efeito"] == "SEM_EFEITO"


def test_cpf_de_compra_cancelada_apagado_apos_a_retencao(
    http: httpx.Client, ambiente: Ambiente, aguardando: dict[str, Any]
) -> None:
    compra_id = aguardando["id"]
    assert notificar(http, ambiente, aguardando["codigo_pagamento"], "CANCELADO").status_code == 200
    esperar(lambda: compra(http, compra_id)["status"] == "CANCELADA", "compra cancelada")
    assert compra(http, compra_id)["cpf"] == "***.982.247-**"

    _mongo(
        f'db.compras.updateOne({{_id: "{compra_id}"}}, '
        "{$set: {atualizada_em: new Date(Date.now() - 91 * 24 * 3600 * 1000)}})"
    )
    _executar_recuperacao()

    assert compra(http, compra_id)["cpf"] is None
    documento = _mongo(f'JSON.stringify(db.compras.findOne({{_id: "{compra_id}"}}).cpf)')
    assert '"cifrado":null' in documento.replace(" ", "")
