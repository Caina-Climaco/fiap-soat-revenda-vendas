# ADR-001: MongoDB como banco do serviço de venda

**Status:** Aceito
**Data:** 2026-11-01

## Contexto

O serviço de venda precisa de banco isolado, diferente do banco do serviço principal (PostgreSQL). Ele atende picos de leitura (vitrine ordenada por preço) e uma escrita concorrente crítica (dois compradores para o mesmo anúncio). Também guarda o estado da saga, que muda a cada passo. Os temas da fase incluem bancos não relacionais (documentos, chave-valor, colunar, grafos) e o Tech Challenge oficial exige pelo menos um banco SQL e um NoSQL na solução.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **MongoDB (documentos)** | O anúncio e a compra são documentos autocontidos; índice composto atende a vitrine; update condicional atômico no documento resolve a reserva | Sem junção nem transação entre coleções no modelo adotado (ver Decisão) |
| Segunda instância de PostgreSQL | Reuso total do código da Fase 3 | Não demonstra NoSQL; o enunciado aceita, mas os temas da fase ficariam de fora |
| Redis (chave-valor) | Leituras muito rápidas | Ordenação paginada e consultas por status são desajeitadas; durabilidade exige configuração extra; ruim para o estado da saga |
| Cassandra (colunar) | Escrita massiva e alta disponibilidade | Pesado demais para um nó local; sem transação nem update condicional simples entre partições |

## Decisão

- **MongoDB 7.0** num StatefulSet com PVC, instância única (standalone). A versão 8.x foi a primeira escolha, mas não sobe em kernel Linux 6.19 ou mais novo (bug do TCMalloc, [SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912), sem correção publicada), e os runners do GitHub Actions já usam esse kernel. A 7.0 tem suporte do fabricante e atende tudo o que o serviço usa (índice único parcial, updates condicionais atômicos).
- **Sem transações entre documentos.** Cada mudança de estado é atômica num único documento (`find_one_and_update` ou `update_one` condicionais, `replace_one` com a revisão lida). O que fica pela metade entre o anúncio e a compra é exatamente o que a saga já sabe tratar: a compra é gravada com o passo T1 marcado como iniciado **antes** da reserva, então uma queda entre as duas gravações é retomada (ou compensada) pela recuperação. Isso dispensa o replica set e mantém o modelo simples.
- Driver **PyMongo** (síncrono, como o SQLAlchemy da Fase 3), atrás de repositórios na camada `infrastructure`; o domínio não conhece o MongoDB.
- **Dinheiro em centavos** (inteiro): ordenação e comparação exatas, sem ponto flutuante. Identificadores como texto (UUID canônico).
- Coleções e índices:

| Coleção | Conteúdo | Índices |
|---|---|---|
| `anuncios` | Réplica do veículo + disponibilidade + `compra_id` + preço e data da venda | `{status, preco_centavos, criado_em}` (vitrine à venda); `{status, preco_venda_centavos, data_venda}` (vendidos) |
| `compras` | Compra (CPF cifrado e mascarado, preço, código do pagamento, datas, status) e o estado da saga, no mesmo documento | Único parcial `{veiculo_id}` com `{ativa: true}` (uma compra ativa por veículo); `{status, saga.proxima_tentativa}` (recuperação) |

- **Controle otimista**: cada documento de compra tem `revisao`. Quem grava confere a revisão lida; se mudou (o repasse do pagamento chegou no meio), relê e decide de novo. Como toda ação da saga é idempotente, reler e repetir é seguro.
- **Lease** no documento da compra (`saga.lease_ate`, `saga.dono_lease`): só um processo executa a saga de uma compra por vez ([ADR-006](ADR-006-expiracao-e-recuperacao-de-sagas.md)).

## Consequências

### Positivas
- Vitrine servida por índice, sem junção.
- Concorrência garantida em duas camadas: índice único parcial (uma compra ativa por veículo) e update condicional na reserva do anúncio.
- A solução passa a ter um banco relacional e um não relacional.

### Negativas
- Código de persistência novo (a Fase 3 era toda SQLAlchemy).
- Instância única: sem alta disponibilidade do banco.
- A consistência entre anúncio e compra depende da saga (não de uma transação), o que exige testar as quedas no meio do caminho.

## Mitigações
- Testes de integração rodam contra o MongoDB 7 real no CI e contra o MongoDB simulado (mongomock) na máquina de quem desenvolve, com a mesma suíte; os cenários de queda no meio da saga são testados explicitamente (`tests/integration/test_recuperacao_e_concorrencia.py`).
- Alta disponibilidade (replica set de três nós) e MongoDB gerenciado (Atlas, DocumentDB) ficam como evolução, assunto da aula de Cloud e DBaaS.
