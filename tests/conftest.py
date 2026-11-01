"""Configuração comum de unit, integration, contrato e bdd.

O CI roda `pytest -m "unit or integration or contrato or bdd"`; para nenhum teste ficar de fora
por esquecimento do marcador, ele é aplicado pelo diretório. Os testes e2e têm configuração própria
(tests/e2e/pytest.ini) e não dependem deste arquivo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_RAIZ = Path(__file__).parent
_MARCADOR_POR_DIRETORIO = {
    "unit": pytest.mark.unit,
    "integration": pytest.mark.integration,
    "contrato": pytest.mark.contrato,
    "bdd": pytest.mark.bdd,
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        try:
            primeira_pasta = item.path.relative_to(_RAIZ).parts[0]
        except ValueError:
            continue
        marcador = _MARCADOR_POR_DIRETORIO.get(primeira_pasta)
        if marcador is not None:
            item.add_marker(marcador)
