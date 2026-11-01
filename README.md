# Revenda de Veículos: serviço de venda

[![CI](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/actions/workflows/ci.yml/badge.svg)](https://github.com/Caina-Climaco/fiap-soat-revenda-vendas/actions/workflows/ci.yml)

Trabalho Substitutivo do Tech Challenge, FIAP PósTech Software Architecture (SOAT), Fase 4.
Autor: Cainã Clímaco, RM366473 (trabalho individual).

> **Esta entrega tem dois repositórios.** Este é o do **serviço de venda** (listagens, compra e orquestração da saga, com MongoDB isolado). O **serviço principal** (cadastro e edição de veículos, cobrança e webhook de pagamento), a plataforma comum e a documentação da solução estão em **[fiap-soat-revenda-principal](https://github.com/Caina-Climaco/fiap-soat-revenda-principal)**.

**Em construção.** Este README cresce a cada PR; a infraestrutura (kind, Terraform, CronJob de recuperação) e o CD entram nos próximos.

## 1. O que é

O serviço isolado que o enunciado pede para os endpoints de listagem e compra, com banco próprio.

| Funcionalidade | Rota |
|---|---|
| Listar veículos à venda, do mais barato ao mais caro | `GET /api/v1/veiculos/a-venda` |
| Listar veículos vendidos, do mais barato ao mais caro | `GET /api/v1/veiculos/vendidos` |
| Consultar um anúncio | `GET /api/v1/anuncios/{veiculo_id}` |
| Comprar um veículo informando o CPF | `POST /api/v1/compras` |
| Consultar a compra (status, código do pagamento, prazo, data da venda, CPF mascarado) | `GET /api/v1/compras/{id}` |
| Réplica do anúncio (chamada pela outbox do principal) | `PUT /internal/v1/anuncios/{veiculo_id}` |
| Resultado do pagamento (repasse do principal) | `POST /internal/v1/compras/{id}/resultado-pagamento` |

A vitrine é uma **réplica de leitura** dos anúncios, alimentada pelo principal; por isso as listagens continuam respondendo com o principal fora do ar.

## 2. A saga da compra

Orquestrada por este serviço ([ADR-002](docs/adrs/ADR-002-saga-orquestrada.md)):

| # | Passo | Onde | Compensação |
|---|---|---|---|
| T1 | Reservar o anúncio (com a versão vista pelo comprador) | aqui | C1 liberar |
| T2 | Bloquear a edição do veículo | principal | C2 desbloquear |
| T3 | Gerar a cobrança (devolve o código do pagamento) | principal | C3 cancelar a cobrança |
| T4 | **Pivô**: cobrança paga (webhook no principal, repassado para cá) | principal | |
| T5 | Efetivar a compra e marcar o anúncio como vendido | aqui | |
| T6 | Marcar o veículo como vendido no catálogo (repetido até concluir) | principal | |

- **Falha antes do pivô** (principal fora, versão alterada, veículo indisponível): a compra é cancelada na hora, o anúncio volta à vitrine e as compensações remotas que falharem ficam pendentes até concluírem.
- **Na espera do pagamento**: expiração ou cancelamento começam pelo C3, porque a cobrança decide a corrida; se ela já estava paga, a compra segue para a efetivação.
- **Durabilidade**: o estado da saga fica no documento da compra; um lease garante um único executor; a recuperação (`python -m vendas.recuperacao`, CronJob a cada minuto) expira reservas, retoma sagas paradas e repete o que foi adiado ([ADR-006](docs/adrs/ADR-006-expiracao-e-recuperacao-de-sagas.md)).
- **Resiliência**: chamadas ao principal com timeout, retry com backoff e jitter (502, 503, 504 e erro de rede) e circuit breaker; com o circuito aberto, a compra falha na hora com 503 e `Retry-After`.

## 3. Como foi implementado

| Tema | Decisão |
|---|---|
| Stack | Python 3.12, FastAPI, Pydantic v2, PyMongo, httpx, cryptography, uv |
| Banco | MongoDB 8: cada mudança de estado é atômica num documento; índice único parcial garante uma compra ativa por veículo ([ADR-001](docs/adrs/ADR-001-mongodb.md)) |
| CPF (LGPD) | Validado (dígitos verificadores), cifrado com AES-256-GCM, mascarado nas respostas, fora de logs, métricas e traces; apagado de compras canceladas após 90 dias ([ADR-005](docs/adrs/ADR-005-cpf-lgpd.md)) |
| Isolamento da saga | Trava semântica, releitura da versão e ordem dos passos ([ADR-004](docs/adrs/ADR-004-contramedidas-de-isolamento.md)) |
| Observabilidade | Logs JSON com `request_id` e `trace_id`; métricas de compras, passos da saga, chamadas ao principal e estado do circuito em `/metrics`; OpenTelemetry quando `OTEL_EXPORTER_OTLP_ENDPOINT` está definido |

```
src/vendas/
  domain/          Anuncio, Compra (com o estado da saga), CPF, portas de repositório
  application/     OrquestradorCompra (saga), casos de uso, portas dos participantes
  infrastructure/  repositórios MongoDB, clientes HTTP do principal, resiliência, cifra
  interfaces/      rotas públicas e internas
  shared/          config, MongoDB, erros RFC 9457, logs, métricas, tracing
  recuperacao.py   CronJob de recuperação de sagas
```

## 4. Como testar localmente

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports

# unidade + integração + BDD; sem TEST_MONGO_URL, o MongoDB é simulado (mongomock)
uv run pytest -m "unit or integration or bdd" --cov=vendas --cov-branch --cov-fail-under=80

# contra um MongoDB real (o CI faz assim, com mongo:8.0)
export TEST_MONGO_URL=mongodb://localhost:27017
uv run pytest -m "unit or integration or bdd"
```

O principal é substituído nos testes por um falso que implementa as rotas internas com o mesmo contrato (`tests/apoio/principal_falso.py`), ligado ao cliente HTTP real: a saga passa pelo retry e pelo circuit breaker de verdade, sem rede. O fluxo completo está em Gherkin em `tests/bdd/features/compra.feature` (caminho feliz, pagamento cancelado, reserva expirada e principal fora do ar).

## 5. Fluxo de contribuição

A `main` é protegida: toda mudança entra por Pull Request, com título em Conventional Commits e os checks do CI verdes (`qualidade`, `testes`, `titulo-pr`).

## 6. Documentação

| Documento | Conteúdo |
|---|---|
| [Modelagem da solução](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/blob/main/docs/02-modelagem-ddd.md) | Contextos, agregados e a saga da compra |
| [Contratos da API](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/blob/main/docs/05-api.md) | Rotas públicas e internas dos dois serviços |
| [ADRs deste serviço](docs/adrs/README.md) | MongoDB, saga orquestrada, réplica de leitura, isolamento, CPF e recuperação de sagas |
