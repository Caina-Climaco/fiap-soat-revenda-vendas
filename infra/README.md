# infra: infraestrutura do serviço de venda

O cluster kind, o Kong e a observabilidade são do repositório **[fiap-soat-revenda-principal](https://github.com/Caina-Climaco/fiap-soat-revenda-principal)**, dono da plataforma comum (ADR-006 da solução). Este repositório só cria o namespace `vendas`.

```text
infra/
├── kind/cluster.yaml   cópia idêntica à do principal (o CI confere)
├── terraform/          namespace vendas, MongoDB 7, segredos e NetworkPolicies
└── runner/             imagem do runner self-hosted do CD (a mesma do principal)
```

| Recurso | Detalhe |
|---|---|
| `vendas-db` | MongoDB 7.0 (StatefulSet com volume de 1 GiB), autenticação ligada; a aplicação usa um usuário só com `readWrite` no banco `revenda_vendas`; `localhost:27017` para a demonstração |
| Segredos | Senhas do MongoDB e chave AES-256 do CPF gerados pelo Terraform (state fora do repositório, `%USERPROFILE%\.revenda\revenda-vendas.tfstate`); tokens de serviço lidos do Secret `principal/contrato-tokens-servico` |
| NetworkPolicies | O banco só aceita a API e o CronJob; a API só aceita o Kong, o principal (outbox) e o Prometheus |

## Ordem do primeiro deploy

1. Repositório principal: `04-subir-ambiente.ps1` e o CD (cluster, Kong, observabilidade, principal).
2. Este repositório: `04-subir-ambiente.ps1`, `03-instalar-runner.ps1` e o CD.
