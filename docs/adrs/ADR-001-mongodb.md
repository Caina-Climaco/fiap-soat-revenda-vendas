# ADR-001: MongoDB como banco do serviço de venda

**Status:** Aceito
**Data:** 2026-11-01

## Contexto

O serviço de venda precisa de banco isolado, diferente do banco do serviço principal (PostgreSQL). Ele atende picos de leitura (vitrine ordenada por preço) e uma escrita concorrente crítica (dois compradores para o mesmo anúncio). Também guarda o estado da saga, que muda a cada passo. Os temas da fase incluem bancos não relacionais (documentos, chave-valor, colunar, grafos) e o Tech Challenge oficial exige pelo menos um banco SQL e um NoSQL na solução.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **MongoDB (documentos)** | O anúncio e a compra são documentos autocontidos; índice composto atende a vitrine; update condicional atômico no documento resolve a reserva; transações multi-documento com replica set | Transações exigem replica set, mesmo com um nó |
| Segunda instância de PostgreSQL | Reuso total do código da Fase 3 | Não demonstra NoSQL; o enunciado aceita, mas os temas da fase ficariam de fora |
| Redis (chave-valor) | Leituras muito rápidas | Ordenação paginada e consultas por status são desajeitadas; durabilidade exige configuração extra; ruim para o estado da saga |
| Cassandra (colunar) | Escrita massiva e alta disponibilidade | Pesado demais para um nó local; sem transação nem update condicional simples entre partições |

## Decisão

- **MongoDB 8.0** num StatefulSet com PVC, configurado como **replica set de um nó** (`rs0`), para habilitar transações multi-documento.
- Driver **PyMongo** (síncrono, como o SQLAlchemy da Fase 3), atrás de repositórios na camada `infrastructure`; o domínio não conhece o MongoDB.
- Coleções e índices:

| Coleção | Conteúdo | Índices |
|---|---|---|
| `anuncios` | Réplica do veículo + disponibilidade + `compra_id` + preço da venda | `{status, preco, criado_em, _id}` para a vitrine à venda; `{status, preco_venda, data_venda, _id}` para a vitrine de vendidos |
| `compras` | Compra (CPF criptografado, preço, código do pagamento, datas, status) | Único parcial `{veiculo_id}` com `{ativa: true}` (uma compra ativa por veículo); único `{codigo_pagamento}` |
| `sagas` | Estado da saga por compra (passo, passos concluídos, tentativas, lease, próxima tentativa) | `{estado, proxima_tentativa}` para a recuperação |

- A reserva (T1) roda numa transação: update condicional do anúncio (`{_id, status: "A_VENDA", versao}` → `RESERVADO`) + inserção da compra + inserção da saga.
- *Write concern* `majority` e *read concern* `majority` nas operações da saga.

## Consequências

### Positivas
- Vitrine servida por índice, sem junção.
- Concorrência garantida em duas camadas: update condicional e índice único parcial.
- A solução passa a ter um banco relacional e um não relacional.

### Negativas
- Código de persistência novo (a Fase 3 era toda SQLAlchemy).
- O replica set de um nó dá transações, mas não alta disponibilidade.
- Os testes de integração precisam de um MongoDB real com replica set (mongomock não suporta transações).

## Mitigações
- Testes de integração com MongoDB em container no CI, iniciado com `--replSet` e `rs.initiate()` no próprio job.
- Alta disponibilidade (três nós) e MongoDB gerenciado (Atlas, DocumentDB) ficam documentados como evolução, assunto da aula de Cloud e DBaaS.
