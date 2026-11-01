"""BDD: mesma montagem dos testes de integração (apoio/ambiente.py)."""

from apoio.ambiente import (  # noqa: F401 - fixtures reexportadas para o pytest
    api,
    app,
    banco,
    cliente_http,
    cliente_mongo,
    componentes,
    principal,
    relogio,
    settings,
)
