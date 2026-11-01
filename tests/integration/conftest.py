"""Integração: fixtures em apoio/ambiente.py (compartilhadas com o BDD)."""

from apoio.ambiente import (  # noqa: F401 - fixtures reexportadas para o pytest
    api,
    app,
    banco,
    cliente_http,
    cliente_mongo,
    componentes,
    principal,
    pytest_report_header,
    relogio,
    settings,
)
