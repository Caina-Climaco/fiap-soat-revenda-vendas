# ADR-005: Tratamento do CPF do comprador (LGPD)

**Status:** Aceito
**Data:** 2026-11-01

## Contexto

Na Fase 3, a API não guardava nenhum dado pessoal direto: o comprador era o `sub` do token e o CPF ficava no Keycloak. Na Fase 4 o enunciado pede que a venda registre o "CPF da pessoa que comprou". O CPF passa a viver no banco do serviço de venda, que é justamente o serviço mais exposto a tráfego público.

Bases legais aplicáveis (LGPD):

- **art. 7º, V**: execução de contrato do qual o titular é parte (a compra e venda);
- **art. 7º, II**: cumprimento de obrigação legal ou regulatória (a identificação do adquirente é exigida em obrigações fiscais e de transferência do veículo).

Princípios aplicáveis:

- **necessidade** (art. 6º, III): só o mínimo necessário;
- **segurança** (art. 6º, VII, e art. 46): medidas técnicas para proteger os dados.

## Alternativas avaliadas

| Alternativa | Prós | Contras |
|---|---|---|
| **CPF criptografado na aplicação (AES-256-GCM), mascarado nas respostas, fora de logs e traces** | Quem lê o banco cru não vê o CPF; controle fino na aplicação | Gestão de chave; sem busca por CPF |
| CPF em claro, confiando no isolamento do banco | Simples | Qualquer vazamento do banco ou de backup expõe o dado |
| Só o hash do CPF | Permite comparar sem revelar | O CPF tem poucos dígitos e é recuperável por força bruta; não serve à obrigação de identificar o adquirente |
| Criptografia nativa do MongoDB (CSFLE / Queryable Encryption) | Transparente ao código | Exige recursos e configuração além do ambiente local; amarra a solução ao fornecedor |

## Decisão

- **Coleta mínima**: a compra pede só o CPF, como o enunciado. Nome, e-mail e telefone não são coletados.
- **Validação**: formato e dígitos verificadores; sequências repetidas (`111.111.111-11`) são recusadas.
- **Em repouso**: o CPF é cifrado com **AES-256-GCM** (biblioteca `cryptography`) antes de ir ao MongoDB, com nonce aleatório por registro e o `id` da compra como dado associado. Junto vai o identificador da chave, para permitir rotação.
- **Chave**: gerada pelo Terraform e entregue como Secret do Kubernetes; nunca versionada.
- **Na resposta**: só a forma mascarada (`***.456.789-**`).
- **Fora de logs, métricas e traces**: um filtro de log e o processador de atributos do Collector removem o campo como segunda barreira. Um teste confere que o CPF não aparece no log de uma compra.
- **Sem busca por CPF**: a compra é consultada só pelo identificador (RN-21).
- **Retenção**: compras canceladas têm o CPF apagado após prazo configurável (padrão 90 dias), executado pelo mesmo CronJob da recuperação de sagas. Compras efetivadas mantêm o CPF enquanto durar a obrigação legal (art. 16, I).

## Consequências

### Positivas
- Um vazamento do banco ou de um backup não expõe CPFs sem a chave.
- O tratamento tem base legal, finalidade e retenção documentadas.

### Negativas
- Perder a chave torna os CPFs irrecuperáveis.
- Não é possível consultar compras por CPF.

## Mitigações
- A chave fica no state do Terraform (fora do repositório). O backup da chave e um cofre (Vault, KMS) ficam como evolução.
- A busca por CPF, se o negócio vier a precisar, seria feita por um índice cego (HMAC com chave separada), registrado aqui como evolução.
