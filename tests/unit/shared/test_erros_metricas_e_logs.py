"""Mensagens de validação em português, métricas Prometheus e níveis do log de acesso."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from vendas.shared.errors import erros_validacao, registrar_tratadores_globais
from vendas.shared.metricas import (
    ROTA_NAO_MAPEADA,
    Metricas,
    MetricasMiddleware,
    PublicadorEventosComMetricas,
    rotulo_do_evento,
)
from vendas.shared.middleware import CorrelacaoMiddleware, nivel_acesso

# ---------------------------------------------------------------- mensagens


@pytest.mark.parametrize(
    ("erro", "esperado"),
    [
        ({"type": "finite_number"}, "deve ser um número finito"),
        (
            {"type": "decimal_type"},
            'deve ser um número decimal (de preferência string, ex.: "79900.00")',
        ),
        ({"type": "decimal_parsing"}, "deve ser um número decimal"),
        ({"type": "int_parsing_size"}, "número inteiro fora do intervalo permitido"),
        ({"type": "int_type"}, "deve ser um número inteiro"),
        (
            {"type": "literal_error", "ctx": {"expected": "'PAGO' or 'CANCELADO'"}},
            "deve ser um de: 'PAGO' ou 'CANCELADO'",
        ),
        ({"type": "string_too_long"}, "valor inválido"),  # ctx ausente: nunca o modelo cru
        ({"type": "tipo_novo_do_pydantic", "msg": "Input should be X"}, "valor inválido"),
        ({"type": "value_error", "msg": "Value error, campo próprio"}, "campo próprio"),
    ],
)
def test_mensagens_de_validacao_sempre_em_portugues(erro: dict[str, Any], esperado: str) -> None:
    assert erros_validacao([{"loc": ("body", "preco"), **erro}]) == [
        {"campo": "preco", "mensagem": esperado}
    ]


# ---------------------------------------------------------------- banco e logs


@pytest.mark.parametrize(
    ("status", "nivel"),
    [
        (200, logging.INFO),
        (404, logging.INFO),
        (401, logging.WARNING),
        (403, logging.WARNING),
        (500, logging.ERROR),
        (503, logging.ERROR),
    ],
)
def test_nivel_do_log_de_acesso(status: int, nivel: int) -> None:
    assert nivel_acesso(status) == nivel


# ---------------------------------------------------------------- métricas


def _amostras(metricas: Metricas) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    texto = metricas.exportar().decode()
    return {
        (a.name, tuple(sorted(a.labels.items()))): a.value
        for familia in text_string_to_metric_families(texto)
        for a in familia.samples
    }


class _Evento:
    def __init__(self, nome: str, **campos: str) -> None:
        self.nome = nome
        self._campos = campos

    def como_dict(self) -> dict[str, str]:
        return self._campos


def test_publicador_com_metricas_repassa_e_conta_por_nome() -> None:
    metricas = Metricas()
    recebidos: list[str] = []

    class Interno:
        def publicar(self, eventos: Any) -> None:
            recebidos.extend(e.nome for e in eventos)

    acoes = {
        "CompraCancelada": lambda e: metricas.compras_canceladas.labels(
            motivo=rotulo_do_evento(e, "motivo")
        ).inc(),
        "CompraIniciada": lambda _e: metricas.compras_iniciadas.inc(),
    }
    publicador = PublicadorEventosComMetricas(Interno(), acoes)
    # Gerador: consumido uma única vez, pelos dois destinos.
    publicador.publicar(
        e
        for e in [
            _Evento("CompraIniciada"),
            _Evento("CompraCancelada", motivo="RESERVA_EXPIRADA"),
            _Evento("CompraCancelada"),
            _Evento("EventoSemMetrica"),
        ]
    )
    assert recebidos == ["CompraIniciada", "CompraCancelada", "CompraCancelada", "EventoSemMetrica"]
    amostras = _amostras(metricas)
    assert amostras[("revenda_compras_iniciadas_total", ())] == 1
    cancelada = "revenda_compras_canceladas_total"
    assert amostras[(cancelada, (("motivo", "RESERVA_EXPIRADA"),))] == 1
    assert amostras[(cancelada, (("motivo", "DESCONHECIDO"),))] == 1
    assert not any(nome.endswith("_created") for nome, _ in amostras)


def test_cada_app_tem_registry_proprio() -> None:
    a, b = Metricas(), Metricas()
    a.compras_iniciadas.inc()
    assert _amostras(a)[("revenda_compras_iniciadas_total", ())] == 1
    assert _amostras(b)[("revenda_compras_iniciadas_total", ())] == 0


def test_middleware_usa_template_da_rota_e_conta_500() -> None:
    metricas = Metricas()
    app = FastAPI()
    registrar_tratadores_globais(app)

    @app.get("/itens/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    @app.get("/quebra")
    def quebra() -> None:
        raise RuntimeError("falha")

    app.add_middleware(MetricasMiddleware, metricas=metricas)
    app.add_middleware(CorrelacaoMiddleware)
    app.add_route("/metrics", metricas.endpoint(), methods=["GET"])

    with TestClient(app) as cliente:  # raise_server_exceptions padrão: nada vaza do app
        assert cliente.get("/itens/abc-123").status_code == 200
        assert cliente.get("/itens/xyz").status_code == 200
        assert cliente.get("/nao/existe/42").status_code == 404
        assert cliente.get("/quebra").status_code == 500
        exposicao = cliente.get("/metrics")
    assert exposicao.headers["content-type"].startswith("text/plain")
    amostras = _amostras(metricas)
    chave = "revenda_http_requisicoes_total"
    rotulos = (("metodo", "GET"), ("rota", "/itens/{item_id}"), ("status", "200"))
    assert amostras[(chave, rotulos)] == 2
    assert (
        amostras[(chave, (("metodo", "GET"), ("rota", ROTA_NAO_MAPEADA), ("status", "404")))] == 1
    )
    assert amostras[(chave, (("metodo", "GET"), ("rota", "/quebra"), ("status", "500")))] == 1
    histograma = ("revenda_http_requisicao_duracao_segundos_count", rotulos)
    assert amostras[histograma] == 2
    assert "abc-123" not in exposicao.text  # ids concretos nunca viram rótulo
