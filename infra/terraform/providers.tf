# O cluster kind e criado FORA do Terraform, pela CLI kind, pelo repositorio principal
# (dono da plataforma, ADR-006 da solucao). Os providers usam o contexto
# kind-<cluster> do kubeconfig que o `kind create cluster` / `kind export kubeconfig`
# grava. Como o cluster ja existe antes do plan, basta um unico `terraform apply`.

locals {
  # Vazio = ~/.kube/config (no Windows, %USERPROFILE%\.kube\config)
  kubeconfig_path = var.kubeconfig_path != "" ? var.kubeconfig_path : pathexpand("~/.kube/config")
  kube_contexto   = "kind-${var.cluster_nome}"
}

provider "kubernetes" {
  config_path    = local.kubeconfig_path
  config_context = local.kube_contexto
}
