# ADR-002: Saga orquestrada pelo serviço de venda

**Status:** Aceito
**Data:** 2026-11-01

## Contexto

A compra altera dados em dois serviços com bancos diferentes: o anúncio e a compra (serviço de venda), o veículo e a cobrança (serviço principal). Não há transação distribuída (2PC) entre MongoDB e PostgreSQL, e ela seria indesejável mesmo se houvesse: travaria recursos dos dois lados durante a espera pelo pagamento, que pode levar até 30 minutos. A comunicação entre os serviços é HTTP, sem broker. O Tech Challenge oficial pede Saga com compensações e a justificativa entre orquestração e coreografia.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **Orquestração pelo serviço de venda** | Um único lugar com o estado e a lógica do processo; fácil de testar, observar e retomar; encaixa naturalmente em HTTP (pedido e resposta) | O orquestrador concentra conhecimento dos passos; o serviço de venda depende das rotas internas do principal |
| Coreografia por eventos | Serviços mais desacoplados; nenhum coordenador | Sem broker, cada serviço teria de chamar o outro por HTTP em cadeia, e o processo ficaria espalhado; difícil saber em que passo uma compra está; compensações cíclicas |
| Orquestrador como terceiro serviço | Separação total | Terceiro repositório, fora do pedido |
| 2PC / XA | Atomicidade | Não existe entre MongoDB e PostgreSQL; bloqueio longo; anti-padrão em microsserviços |

## Decisão

- A **saga da compra é orquestrada pelo serviço de venda**, dono do subdomínio principal e da compra.
- O orquestrador é um *process manager* explícito na camada `application` (`OrquestradorCompra`). Ele lê o estado da `SagaCompra`, decide o próximo passo, chama o participante por uma porta (`CatalogoPort`, `PagamentosPort`) e grava o novo estado.
- Passos ([docs/02 da solução, seção 2.8](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/blob/main/docs/02-modelagem-ddd.md#28-saga-da-compra)):

| Passo | Ação | Tipo | Compensação |
|---|---|---|---|
| T1 | Reservar anúncio | compensável | C1 liberar |
| T2 | Bloquear veículo | compensável | C2 desbloquear |
| T3 | Gerar cobrança | compensável | C3 cancelar cobrança |
| T4 | Cobrança paga | **pivô** (decidido no principal) | |
| T5 | Efetivar compra | retentável | |
| T6 | Marcar veículo vendido | retentável | |

- **Gravar antes de chamar**: o passo é marcado como "em andamento" antes da chamada externa e "concluído" depois da resposta. Uma queda entre os dois deixa a saga num estado que a recuperação sabe tratar, porque toda operação é idempotente pela `compra_id`.
- **Compensação em ordem inversa** e só dos passos concluídos ou em andamento (compensar um passo que não aconteceu é inofensivo, pela idempotência).
- **Depois do pivô não se compensa**: falhas em T5 e T6 são repetidas até concluir.
- **Antes do pivô, a falha cancela na hora**: o comprador ainda não tem o código do pagamento, então a compra é cancelada assim que T2 ou T3 falham (503 ou 409 na resposta). A liberação do anúncio (C1, local) roda imediatamente; as compensações remotas que falharem ficam pendentes, com backoff, até concluírem. Devolver o anúncio antes do C2 é seguro por causa da regra contra ressurreição do principal.
- **Na espera do pagamento, o C3 decide**: expiração ou cancelamento do processador começam pelo cancelamento da cobrança. Se o principal responde que a cobrança já foi paga, o pivô aconteceu e a saga segue para T5 e T6; se o principal não responde, a compra continua aguardando e a decisão é tentada de novo.
- Cada transição gera log estruturado, métrica (`saga_transicoes_total{passo, resultado}`) e span no trace.

## Consequências

### Positivas
- O estado de qualquer compra é consultável num único documento.
- O vídeo mostra a saga inteira e as compensações num único trace.
- Testes unitários do orquestrador com portas falsas cobrem todas as combinações de falha.

### Negativas
- O serviço de venda conhece a sequência de operações do principal (acoplamento de processo).
- Enquanto a saga não termina, o sistema fica em estados intermediários visíveis (consistência eventual).

## Mitigações
- O acoplamento fica restrito às portas e é vigiado pelos testes de contrato.
- Os estados intermediários são travas semânticas intencionais ([ADR-004](ADR-004-contramedidas-de-isolamento.md)).
- Recuperação automática de sagas paradas ([ADR-006](ADR-006-expiracao-e-recuperacao-de-sagas.md)).
