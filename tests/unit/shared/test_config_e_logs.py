"""Configuração, logs JSON (com ids do trace) e relógio."""

from __future__ import annotations

import base64
import io
import json
import logging
import sys
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from vendas.shared.clock import RelogioFixo, RelogioSistema
from vendas.shared.config import MongoSettings, Settings
from vendas.shared.eventos import PublicadorEventosLog
from vendas.shared.logging import (
    CPF_MASCARADO,
    FormatadorJson,
    configurar_logs,
    mascarar_cpf,
    request_id_atual,
)

VARIAVEIS = (
    "MONGO_URL",
    "MONGO_BANCO",
    "PRINCIPAL_URL",
    "TOKEN_SERVICO_ENTRADA",
    "TOKEN_SERVICO_SAIDA",
    "CPF_CHAVE",
    "CPF_CHAVE_ID",
    "PRAZO_RESERVA_MINUTOS",
    "LOG_LEVEL",
)
CHAVE = base64.b64encode(bytes(32)).decode()


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)


def test_settings_le_variaveis_do_contrato(monkeypatch: pytest.MonkeyPatch) -> None:
    valores = {
        "MONGO_URL": "mongodb://revenda-vendas-db.vendas.svc.cluster.local:27017",
        "MONGO_BANCO": "revenda_vendas",
        "PRINCIPAL_URL": "http://revenda-principal.principal.svc.cluster.local:8000/",
        "TOKEN_SERVICO_ENTRADA": "e" * 32,
        "TOKEN_SERVICO_SAIDA": "s" * 32,
        "CPF_CHAVE": CHAVE,
        "CPF_CHAVE_ID": "k2",
        "PRAZO_RESERVA_MINUTOS": "5",
        "LOG_LEVEL": "debug",
    }
    for nome, valor in valores.items():
        monkeypatch.setenv(nome, valor)
    settings = Settings()
    assert settings.mongo_url.get_secret_value().startswith("mongodb://revenda-vendas-db")
    assert settings.principal_url == "http://revenda-principal.principal.svc.cluster.local:8000"
    assert settings.chave_cpf_bytes == bytes(32)
    assert (settings.cpf_chave_id, settings.prazo_reserva_minutos) == ("k2", 5)
    assert settings.log_level == "DEBUG"
    assert MongoSettings().mongo_banco == "revenda_vendas"


@pytest.mark.parametrize(
    "chave", ["nao-e-base64!", base64.b64encode(bytes(16)).decode()], ids=["base64", "tamanho"]
)
def test_chave_do_cpf_precisa_ser_aes_256(chave: str) -> None:
    with pytest.raises(ValidationError, match="CPF_CHAVE"):
        Settings(token_servico_entrada="e" * 16, token_servico_saida="s" * 16, cpf_chave=chave)  # type: ignore[arg-type]


def test_tokens_sao_obrigatorios() -> None:
    with pytest.raises(ValidationError):
        Settings(cpf_chave=CHAVE)  # type: ignore[arg-type]


def test_formatador_json_inclui_request_id_e_campos() -> None:
    registro = logging.LogRecord("vendas.teste", logging.INFO, __file__, 1, "olá %s", ("x",), None)
    registro.campos = {"evento": "CobrancaPaga"}
    registro.extra_livre = 7
    registro.color_message = "\x1b[36mcolorido\x1b[0m"  # anexado pelo uvicorn
    token = request_id_atual.set("req-1")
    try:
        saida = json.loads(FormatadorJson().format(registro))
    finally:
        request_id_atual.reset(token)
    assert saida["mensagem"] == "olá x"
    assert saida["request_id"] == "req-1"
    assert saida["evento"] == "CobrancaPaga"
    assert saida["extra_livre"] == 7
    assert saida["nivel"] == "INFO"
    assert "color_message" not in saida


def test_formatador_json_registra_excecao() -> None:
    try:
        raise RuntimeError("falhou")
    except RuntimeError:
        registro = logging.LogRecord("x", logging.ERROR, __file__, 1, "erro", (), sys.exc_info())
    saida = json.loads(FormatadorJson().format(registro))
    assert "RuntimeError: falhou" in saida["excecao"]


def test_formatador_json_mascara_cpf_em_qualquer_parte_da_linha() -> None:
    """Segunda barreira do ADR-005: CPF que escapar por engano não chega ao log."""
    try:
        raise ValueError("CPF 52998224725 recusado")
    except ValueError:
        excecao = sys.exc_info()
    registro = logging.LogRecord(
        "x", logging.ERROR, __file__, 1, "compra de %s", ("529.982.247-25",), excecao
    )
    registro.campos = {"cpf": "529982247-25", "compra_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"}
    linha = FormatadorJson().format(registro)
    assert "52998224725" not in linha
    assert "982.247" not in linha
    saida = json.loads(linha)
    assert saida["mensagem"] == f"compra de {CPF_MASCARADO}"
    assert saida["cpf"] == CPF_MASCARADO
    assert CPF_MASCARADO in saida["excecao"]
    assert saida["compra_id"] == "3fa85f64-5717-4562-b3fc-2c963f66afa6"


def test_formatador_json_continua_valido_com_inteiro_de_11_digitos() -> None:
    """O filtro roda antes da serialização: o número mascarado vira string, e a linha é JSON."""
    registro = logging.LogRecord("x", logging.INFO, __file__, 1, "preco alto", (), None)
    registro.campos = {
        "preco_centavos": 12345678901,
        "ids": [52998224725, 7],
        "detalhe": {"telefone": "15998765432", "ativo": True, "nota": 9.5, "vazio": None},
        "tupla": (1, "529.982.247-25"),
        "objeto": _Repr("cpf 52998224725"),
    }
    linha = FormatadorJson().format(registro)
    saida = json.loads(linha)
    assert saida["preco_centavos"] == CPF_MASCARADO
    assert saida["ids"] == [CPF_MASCARADO, 7]
    assert saida["detalhe"] == {
        "telefone": CPF_MASCARADO,
        "ativo": True,
        "nota": 9.5,
        "vazio": None,
    }
    assert saida["tupla"] == [1, CPF_MASCARADO]
    assert saida["objeto"] == f"cpf {CPF_MASCARADO}"


class _Repr:
    def __init__(self, texto: str) -> None:
        self.texto = texto

    def __str__(self) -> str:
        return self.texto


@pytest.mark.parametrize(
    "texto",
    [
        "trace 4bf92f3577b34da6a3ce929d0e0e4736",
        "2026-11-01T22:49:20.123+00:00",
        "valor 123456789012",
        "preco 52998224725.50",
        "versao 1.52998224725",
    ],
)
def test_mascara_nao_altera_o_que_nao_e_cpf(texto: str) -> None:
    assert mascarar_cpf(texto) == texto


def test_configurar_logs_e_publicador_de_eventos() -> None:
    configurar_logs("INFO")
    raiz = logging.getLogger()
    buffer = io.StringIO()
    raiz.handlers[0].setStream(buffer)  # type: ignore[attr-defined]

    class Evento:
        nome = "VeiculoCadastrado"

        def como_dict(self) -> dict[str, str]:
            return {"veiculo_id": "v1"}

    PublicadorEventosLog().publicar([Evento()])
    linha = json.loads(buffer.getvalue().strip())
    assert linha["evento"] == "VeiculoCadastrado"
    assert linha["veiculo_id"] == "v1"
    assert logging.getLogger("uvicorn.access").disabled


def test_relogios() -> None:
    assert RelogioSistema().agora().tzinfo is UTC
    relogio = RelogioFixo(datetime(2026, 1, 1, tzinfo=UTC))
    relogio.avancar(timedelta(minutes=5))
    assert relogio.agora() == datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    relogio.definir(datetime(2027, 1, 1, tzinfo=UTC))
    assert relogio.agora().year == 2027
    with pytest.raises(ValueError, match="timezone"):
        RelogioFixo(datetime(2026, 1, 1))
