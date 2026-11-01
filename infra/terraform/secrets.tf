# Segredos do servico de venda. Nomes e chaves sao contrato com os manifestos (k8s/) e o CD.
# Os valores ficam so no state local (%USERPROFILE%\.revenda\revenda-vendas.tfstate) e nos
# Secrets do namespace vendas. Senhas so com letras e digitos (seguras em URL).

# CONTRATO com o repositorio principal: os dois tokens de servico sao gerados la (um por
# direcao) e publicados neste Secret. Sem ele (principal nao implantado) o plan falha.
data "kubernetes_secret_v1" "contrato_tokens_servico" {
  metadata {
    name      = "contrato-tokens-servico"
    namespace = var.namespace_principal
  }
}

resource "random_password" "mongo_root" {
  length  = 32
  special = false
}

resource "random_password" "mongo_app" {
  length  = 32
  special = false
}

# Chave AES-256 do CPF (ADR-005 deste servico). Trocar a chave torna ilegiveis os CPFs ja
# gravados: a rotacao exige nova chave com outro CPF_CHAVE_ID e recifragem (evolucao).
resource "random_bytes" "cpf_chave" {
  length = 32
}

locals {
  mongo_app_usuario = "vendas"
  mongo_banco       = "revenda_vendas"
  mongo_url = format(
    "mongodb://%s:%s@vendas-db.%s.svc.cluster.local:27017/%s?authSource=%s",
    local.mongo_app_usuario,
    random_password.mongo_app.result,
    kubernetes_namespace_v1.vendas.metadata[0].name,
    local.mongo_banco,
    local.mongo_banco,
  )
}

# Usado so pelo MongoDB: usuario root e o usuario da aplicacao criado na inicializacao
resource "kubernetes_secret_v1" "vendas_db_credentials" {
  metadata {
    name      = "vendas-db-credentials"
    namespace = kubernetes_namespace_v1.vendas.metadata[0].name
    labels    = local.rotulos_comuns
  }
  type = "Opaque"
  data = {
    MONGO_INITDB_ROOT_USERNAME = "root"
    MONGO_INITDB_ROOT_PASSWORD = random_password.mongo_root.result
    MONGO_APP_USUARIO          = local.mongo_app_usuario
    MONGO_APP_SENHA            = random_password.mongo_app.result
    MONGO_APP_BANCO            = local.mongo_banco
  }
}

# Usado pela API e pelo CronJob: so o usuario da aplicacao (readWrite em revenda_vendas)
resource "kubernetes_secret_v1" "vendas_mongo" {
  metadata {
    name      = "vendas-mongo"
    namespace = kubernetes_namespace_v1.vendas.metadata[0].name
    labels    = local.rotulos_comuns
  }
  type = "Opaque"
  data = {
    MONGO_URL = local.mongo_url
  }
}

resource "kubernetes_secret_v1" "vendas_cpf" {
  metadata {
    name      = "vendas-cpf"
    namespace = kubernetes_namespace_v1.vendas.metadata[0].name
    labels    = local.rotulos_comuns
  }
  type = "Opaque"
  data = {
    CPF_CHAVE    = random_bytes.cpf_chave.base64
    CPF_CHAVE_ID = "k1"
  }
}

# Papeis invertidos em relacao ao principal: o que ele envia e o que este servico recebe.
resource "kubernetes_secret_v1" "vendas_tokens_servico" {
  metadata {
    name      = "vendas-tokens-servico"
    namespace = kubernetes_namespace_v1.vendas.metadata[0].name
    labels    = local.rotulos_comuns
  }
  type = "Opaque"
  data = {
    TOKEN_SERVICO_ENTRADA = data.kubernetes_secret_v1.contrato_tokens_servico.data["PRINCIPAL_PARA_VENDAS"]
    TOKEN_SERVICO_SAIDA   = data.kubernetes_secret_v1.contrato_tokens_servico.data["VENDAS_PARA_PRINCIPAL"]
  }
}
