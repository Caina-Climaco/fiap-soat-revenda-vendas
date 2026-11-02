"""Recuperação de sagas: `python -m vendas.recuperacao` (CronJob a cada minuto, ADR-006).

Em uma execução: expira reservas vencidas (C3 como árbitro), retoma sagas paradas por queda
de pod, repete passos e compensações adiados e apaga o CPF de compras canceladas além do
prazo de retenção. Termina com código 0 mesmo se o principal estiver fora: o que não deu
certo fica agendado para a próxima execução.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from vendas.composicao import montar
from vendas.infrastructure.repositorio_mongo import garantir_indices
from vendas.main import criar_cliente_principal
from vendas.shared.clock import RelogioSistema
from vendas.shared.config import Settings
from vendas.shared.eventos import PublicadorEventosLog
from vendas.shared.logging import configurar_logs
from vendas.shared.mongo import BancoMongo

_logger = logging.getLogger("vendas.recuperacao")


def main(_argv: Sequence[str] | None = None, *, banco: BancoMongo | None = None) -> int:
    settings = Settings()
    configurar_logs(settings.log_level)
    banco_proprio = banco is None
    banco = banco or BancoMongo.conectar(
        settings.mongo_url.get_secret_value(), settings.mongo_banco
    )
    cliente = criar_cliente_principal(settings, None)
    try:
        garantir_indices(banco.db)
        componentes = montar(settings, banco, cliente, RelogioSistema(), PublicadorEventosLog())
        resultado = componentes.recuperar.executar()
    finally:
        cliente.fechar()
        if banco_proprio:
            banco.fechar()
    _logger.info(
        "recuperação concluída",
        extra={
            "campos": {
                "sagas_processadas": resultado.sagas_processadas,
                "cpfs_apagados": resultado.cpfs_apagados,
                "sagas_paradas": resultado.sagas_paradas,
            }
        },
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - ponto de entrada do CronJob
    raise SystemExit(main(sys.argv[1:]))
