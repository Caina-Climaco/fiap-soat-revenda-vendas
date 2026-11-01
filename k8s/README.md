# k8s: manifestos do serviço de venda (kustomize)

```text
k8s/
├── base/          ConfigMap revenda-vendas-config, Deployment revenda-vendas, Service ClusterIP 8000, HPA 2..5
└── recuperacao/   CronJob revenda-vendas-recuperacao (python -m vendas.recuperacao, a cada minuto)
```

Tudo no namespace `vendas`. O namespace, o MongoDB (`vendas-db`), os Secrets e as NetworkPolicies vêm do Terraform em `infra/terraform`; estes manifestos só **referenciam** os Secrets:

| Secret | Chaves |
|---|---|
| `vendas-mongo` | `MONGO_URL` (usuário da aplicação, só `readWrite` em `revenda_vendas`) |
| `vendas-cpf` | `CPF_CHAVE` (AES-256 em base64), `CPF_CHAVE_ID` |
| `vendas-tokens-servico` | `TOKEN_SERVICO_ENTRADA` (principal para venda), `TOKEN_SERVICO_SAIDA` (venda para principal) |

## Contrato com o CD (`.github/workflows/cd.yml`)

| Item | Valor |
|---|---|
| Imagem no repositório | `revenda-vendas:dev` (tag neutra), trocada no CD por `revenda-vendas:<sha>` |
| Ordem | 1) aplica `k8s/base` e espera o rollout; 2) aplica `k8s/recuperacao` e dispara uma execução de fumaça (`kubectl create job --from=cronjob/...`) |
| Containers | `api` no Deployment, `recuperacao` no CronJob |
