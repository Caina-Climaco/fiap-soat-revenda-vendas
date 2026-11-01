# contratos: rotas internas entre os dois serviços

| Arquivo | Papel aqui | Origem |
|---|---|---|
| `vendas-interno.openapi.json` | **Provedor**: o que este serviço promete nas rotas `/internal/v1` | Gerado da aplicação: `cd tests && PYTHONPATH=. uv run python -m contrato.gerar` |
| `principal-interno.openapi.json` | **Consumidor**: o que este serviço espera do servico principal | Cópia do arquivo publicado na `main` de [fiap-soat-revenda-principal](https://github.com/Caina-Climaco/fiap-soat-revenda-principal) |

Cada contrato é o recorte `/internal/v1` da OpenAPI do serviço, com os esquemas referenciados (inclusive o `Problema` das recusas, RFC 9457).

## O que o job `contrato` do CI garante

1. **Provedor**: o contrato publicado é exatamente o da aplicação atual, e as respostas reais das rotas internas (sucesso, repetição idempotente e recusas) cumprem o contrato.
2. **Consumidor**: toda chamada que este serviço faz ao outro cumpre a cópia do contrato dele; o dublê do outro serviço usado nos testes também só responde o que o contrato permite, então ele não diverge do serviço real.
3. **Sincronia**: a cópia local é idêntica à publicada na `main` do outro repositório.

Mudar uma rota interna exige, no mesmo dia, um PR no provedor (contrato novo) e outro no consumidor (cópia nova e o cliente ajustado). Mudanças compatíveis (campo opcional novo) passam nos dois lados; incompatíveis quebram o job `contrato` de quem ficou para trás.
