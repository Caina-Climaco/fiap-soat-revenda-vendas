# Architecture Decision Records do serviço de venda

Decisões próprias do serviço de venda. As decisões da solução (decomposição, comunicação HTTP com outbox, resiliência, autorização, plataforma, tracing e qualidade) estão no [repositório principal](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/tree/main/docs/adrs).

| Nº | Decisão | Status |
|---|---|---|
| [ADR-001](ADR-001-mongodb.md) | MongoDB como banco do serviço de venda | Aceito |
| [ADR-002](ADR-002-saga-orquestrada.md) | Saga orquestrada pelo serviço de venda | Aceito |
| [ADR-003](ADR-003-replica-de-leitura-dos-anuncios.md) | Réplica de leitura dos anúncios no serviço de venda | Aceito |
| [ADR-004](ADR-004-contramedidas-de-isolamento.md) | Contramedidas para a falta de isolamento na saga | Aceito |
| [ADR-005](ADR-005-cpf-lgpd.md) | Tratamento do CPF do comprador (LGPD) | Aceito |
| [ADR-006](ADR-006-expiracao-e-recuperacao-de-sagas.md) | Expiração da reserva e recuperação de sagas | Aceito (atualiza o ADR-009 da Fase 3) |
