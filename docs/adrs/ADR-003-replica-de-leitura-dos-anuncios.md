# ADR-003: Réplica de leitura dos anúncios no serviço de venda

**Status:** Aceito
**Data:** 2026-11-01

## Contexto

As listagens ficam no serviço de venda, mas os veículos são cadastrados e editados no serviço principal. O serviço de venda precisa dos dados dos veículos para montar a vitrine e para congelar preço e descrição na compra. O motivo do isolamento é suportar picos de chamadas, então a vitrine não pode depender do principal a cada requisição.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **Réplica de leitura (CQRS) alimentada pelo principal via outbox + HTTP** | Vitrine independente do principal; consulta por índice local; padrão visto na aula de queries em microsserviços | Consistência eventual; o serviço de venda guarda uma cópia dos dados |
| API Composition (o serviço de venda consulta o principal a cada listagem) | Dados sempre atuais | A vitrine cai com o principal; o pico de listagem vira pico no principal; desfaz o motivo do isolamento |
| Cache com expiração (TTL) | Simples | Dados velhos por até um TTL; o primeiro acesso após expirar ainda depende do principal |

## Decisão

- O principal publica o anúncio a cada cadastro e edição, pela outbox ([ADR-003 da solução](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/blob/main/docs/adrs/ADR-003-http-com-outbox.md)), em `PUT /internal/v1/anuncios/{veiculo_id}` com `versao`, marca, modelo, ano, cor e preço.
- O serviço de venda aplica a mensagem **só se a versão recebida for maior que a gravada** (update condicional por versão). Mensagens repetidas ou fora de ordem são ignoradas com 200.
- Um anúncio novo entra como `A_VENDA`. A disponibilidade (`RESERVADO`, `VENDIDO`) é do serviço de venda e não é sobrescrita pela réplica.
- A listagem devolve `versao` em cada item. O pedido de compra leva essa versão, e o bloqueio no Catálogo a confere ([ADR-004](ADR-004-contramedidas-de-isolamento.md)). Assim a consistência eventual da vitrine nunca resulta numa compra por preço desatualizado.
- **Reconstrução**: um comando administrativo do principal republica todos os veículos na outbox, para popular uma réplica vazia (banco novo ou perda de dados).

## Consequências

### Positivas
- Vitrine e consulta de compra funcionam com o principal fora do ar (RN-22), o que é demonstrável no vídeo.
- Listagens servidas por índice local, sem rede.

### Negativas
- Logo após um cadastro ou edição, a vitrine pode mostrar a versão anterior por um instante.
- Dados descritivos do veículo existem nos dois bancos.

## Mitigações
- A janela de atraso é medida (métrica de idade da última réplica aplicada) e normalmente fica abaixo de 1 s.
- A duplicação é intencional e só de dados públicos (nenhum dado pessoal é replicado).
