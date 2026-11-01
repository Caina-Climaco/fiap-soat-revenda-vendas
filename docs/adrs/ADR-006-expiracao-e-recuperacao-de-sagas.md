# ADR-006: Expiração da reserva e recuperação de sagas

**Status:** Aceito
**Data:** 2026-11-01
**Atualiza:** ADR-009 da Fase 3 (expiração preguiçosa)

## Contexto

Na Fase 3, a expiração da reserva era preguiçosa: aplicada em cada leitura e escrita, mais um CronJob de saneamento a cada 10 minutos. Era barata porque cancelar era uma transação local.

Na Fase 4, expirar uma compra é executar compensações em outro serviço (cancelar a cobrança, desbloquear o veículo). Fazer isso dentro de uma listagem pública colocaria chamadas HTTP no caminho mais quente do serviço. Além disso, uma saga pode parar no meio por queda de pod, e alguém precisa retomá-la.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **CronJob de recuperação a cada minuto + lease no documento da saga** | Fora do caminho das requisições; reaproveita o modelo da Fase 3; um só mecanismo para expiração, retomada e retentativas | Granularidade de 1 minuto |
| Expiração preguiçosa nas leituras, como na Fase 3 | Sem processo extra | Chamadas HTTP dentro da listagem; não resolve sagas paradas |
| Laço em segundo plano dentro da API | Granularidade de segundos | Com várias réplicas, precisa de eleição ou lease; mistura carga de fundo com o atendimento |
| Agendador externo (fila com atraso) | Preciso | Exige broker, contra o enunciado |

## Decisão

- CronJob `revenda-vendas-recuperacao` a cada minuto (`concurrencyPolicy: Forbid`), executando `python -m vendas.recuperacao`, que trata em lotes:

| Situação | Ação |
|---|---|
| Compra `AGUARDANDO_PAGAMENTO` com `expira_em` vencido | Executa C3; se a cobrança já estiver paga, segue para T5 e T6; senão, C2 e C1 |
| Saga em andamento com lease vencido (pod caiu no meio) | Retoma: compensa se não passou do pivô; avança se passou |
| Passo retentável pendente (T6 com o principal fora) | Repete, com backoff registrado em `proxima_tentativa` |
| Compra cancelada além do prazo de retenção | Apaga o CPF ([ADR-005](ADR-005-cpf-lgpd.md)) |

- **Lease**: antes de agir numa saga, quem age (requisição ou CronJob) toma posse com um update condicional (`lease_ate < agora` → `lease_ate = agora + 30 s`, `dono = <pod>`). Isso impede que a requisição de compra e o CronJob executem a mesma saga ao mesmo tempo.
- **Expiração imediata no caminho da compra**: se um comprador tenta comprar um anúncio cuja reserva já venceu, a própria requisição expira a compra anterior (com lease) antes de reservar. Isso mantém a vantagem da Fase 3 sem custo nas listagens.
- O prazo da reserva (padrão 30 min) e o intervalo do CronJob são configuráveis; no e2e e no vídeo o prazo é reduzido para a demonstração.

## Consequências

### Positivas
- Nenhuma chamada HTTP no caminho das listagens.
- Expiração, retomada após falha e retentativas compartilham um único mecanismo testado.

### Negativas
- Um anúncio com reserva vencida pode continuar fora da vitrine por até cerca de 1 minuto.
- A leitura direta do banco pode mostrar compras vencidas ainda como `AGUARDANDO_PAGAMENTO` até o CronJob passar.

## Mitigações
- A consulta da compra informa `expira_em`, então o atraso não engana quem consulta.
- Métrica de sagas paradas e alerta quando alguma fica sem progresso por mais de 5 minutos.
