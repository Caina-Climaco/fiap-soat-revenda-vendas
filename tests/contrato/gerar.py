"""Gera o contrato publicado por este servico: `uv run python -m contrato.gerar`
(com `tests` no PYTHONPATH, como o pytest faz). Rode ao mudar uma rota interna."""

from __future__ import annotations

import base64
from typing import Any

import mongomock

from contrato.openapi_contrato import DIRETORIO, recortar, serializar
from vendas.main import criar_app
from vendas.shared.config import Settings
from vendas.shared.mongo import BancoMongo

ARQUIVO = "vendas-interno.openapi.json"
TITULO = "Revenda de Veiculos: rotas internas do servico de venda"


def contrato_atual() -> dict[str, Any]:
    settings = Settings(
        token_servico_entrada="token-so-para-gerar-o-contrato",
        token_servico_saida="token-so-para-gerar-o-contrato",
        cpf_chave=base64.b64encode(bytes(32)).decode(),
    )
    banco = BancoMongo(mongomock.MongoClient(tz_aware=True), "contrato")
    return recortar(criar_app(settings, banco=banco).openapi(), TITULO)


def main() -> None:
    (DIRETORIO / ARQUIVO).write_text(serializar(contrato_atual()), encoding="utf-8")
    print(f"contrato gravado em contratos/{ARQUIVO}")


if __name__ == "__main__":
    main()
