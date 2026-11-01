locals {
  rotulos_comuns = {
    "app.kubernetes.io/part-of"    = "revenda-veiculos"
    "app.kubernetes.io/managed-by" = "terraform"
  }
}

# Servico de venda (API, vendas-db e CronJob de recuperacao). Os namespaces principal,
# gateway e observabilidade sao do repositorio fiap-soat-revenda-principal (ADR-006 da
# solucao); daqui so se le o Secret de contrato dos tokens (secrets.tf).
resource "kubernetes_namespace_v1" "vendas" {
  metadata {
    name   = "vendas"
    labels = local.rotulos_comuns
  }
}
