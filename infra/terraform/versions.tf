# Versoes do Terraform e dos providers (docs/08-ci-cd.md).
# Sem provider de cluster: o kind e criado pela CLI kind (infra/kind/cluster.yaml, ADR-006).
# Os dois providers abaixo sao assinados (Authenticode) pela HashiCorp e rodam com o
# Smart App Control do Windows 11 ligado.
# As restricoes "~>" aceitam apenas correcoes/minors compativeis; o arquivo
# .terraform.lock.hcl (gerado no primeiro `terraform init`) deve ser versionado.

terraform {
  required_version = ">= 1.16.0"

  required_providers {
    # Namespace, Secrets, StatefulSet, Service e NetworkPolicy
    # (recursos tipados *_v1; a serie 3.x deprecou os recursos sem sufixo).
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.3"
    }
    # Senhas do MongoDB e chave do CPF (random_bytes).
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  # State local FORA do repositorio. O caminho vem na inicializacao (configuracao parcial):
  #   terraform init -backend-config="path=$USERPROFILE/.revenda/revenda-vendas.tfstate"
  # O state contem os segredos gerados em texto claro: nunca versionar.
  backend "local" {}
}
