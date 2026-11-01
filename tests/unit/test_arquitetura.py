"""Regras de dependência verificadas pelo código-fonte (complementa o import-linter)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "vendas"
FRAMEWORKS = (
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_settings",
    "pymongo",
    "httpx",
    "cryptography",
    "opentelemetry",
)


def _importados(arquivo: Path) -> set[str]:
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module and no.level == 0:
            nomes.add(no.module)
    return nomes


def _viola(importados: set[str], proibidos: tuple[str, ...]) -> list[str]:
    return sorted(i for i in importados for p in proibidos if i == p or i.startswith(p + "."))


@pytest.mark.parametrize(
    ("camada", "proibidos"),
    [
        ("domain", (*FRAMEWORKS, "vendas.shared", "vendas.application", "vendas.infrastructure")),
        ("application", (*FRAMEWORKS, "vendas.infrastructure", "vendas.interfaces")),
        ("shared", ("vendas.domain", "vendas.application", "vendas.infrastructure")),
    ],
)
def test_dependencias_por_camada(camada: str, proibidos: tuple[str, ...]) -> None:
    arquivos = sorted((SRC / camada).rglob("*.py"))
    assert arquivos
    for arquivo in arquivos:
        assert _viola(_importados(arquivo), proibidos) == [], arquivo
