variable "cluster_nome" {
  description = "Nome do cluster kind (criado pelo repositorio principal ou por este CD); os providers usam o contexto kind-<nome>."
  type        = string
  default     = "revenda"
}

variable "kubeconfig_path" {
  description = "Kubeconfig com o contexto kind-<cluster_nome> (gravado pela CLI kind). Vazio = ~/.kube/config (no Windows, %USERPROFILE%\\.kube\\config)."
  type        = string
  default     = ""
}

variable "pod_subnet" {
  description = "Faixa de IPs dos pods no kind: deve ser igual a networking.podSubnet de infra/kind/cluster.yaml (usada nas NetworkPolicies)."
  type        = string
  default     = "10.244.0.0/16"
}

variable "expor_banco" {
  description = <<-EOT
    Publica o vendas-db (MongoDB) no host (localhost:27017 -> NodePort 30017) para a
    demonstracao do banco no video. O mapeamento de porta do kind existe sempre; esta
    variavel so muda o tipo do Service e a regra extra da NetworkPolicy.
  EOT
  type        = bool
  default     = true
}

variable "namespace_principal" {
  description = "Namespace do servico principal (outro repositorio, ADR-006 da solucao): dono do Secret de contrato dos tokens e origem da outbox."
  type        = string
  default     = "principal"
}

variable "mongo_imagem" {
  description = "Imagem oficial do MongoDB 7.0 (fixada por versao; o 8.x nao sobe em kernels 6.19+, ADR-001)."
  type        = string
  default     = "mongo:7.0.43"
}

variable "mongo_cache_gb" {
  description = "Cache do WiredTiger em GB (o padrao do MongoDB usaria metade da memoria do no)."
  type        = number
  default     = 0.25
}
