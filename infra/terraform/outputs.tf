# Nenhuma saida contem senha (nem como sensitive): os valores ficam so no state e nos
# Secrets do cluster.

output "cluster" {
  description = "Nome do cluster kind e contexto do kubectl."
  value = {
    nome            = var.cluster_nome
    contexto        = local.kube_contexto
    kubeconfig_path = local.kubeconfig_path
  }
}

output "urls" {
  description = "Enderecos locais (apenas 127.0.0.1)."
  value = {
    vitrine   = "http://localhost:8080/api/v1/veiculos/a-venda (pelo Kong do repositorio principal)"
    swagger   = "http://localhost:8080/vendas/docs"
    vendas_db = var.expor_banco ? "localhost:27017 (MongoDB; usuario e senha com o comando em comandos_uteis)" : "nao exposto (use kubectl exec)"
  }
}

output "secrets" {
  description = "Secrets criados (namespace/nome => chaves). Contrato com os manifestos k8s/ e o CD."
  value = {
    "vendas/vendas-db-credentials" = ["MONGO_INITDB_ROOT_USERNAME", "MONGO_INITDB_ROOT_PASSWORD", "MONGO_APP_USUARIO", "MONGO_APP_SENHA", "MONGO_APP_BANCO"]
    "vendas/vendas-mongo"          = ["MONGO_URL"]
    "vendas/vendas-cpf"            = ["CPF_CHAVE", "CPF_CHAVE_ID"]
    "vendas/vendas-tokens-servico" = ["TOKEN_SERVICO_ENTRADA", "TOKEN_SERVICO_SAIDA"]
  }
  depends_on = [
    kubernetes_secret_v1.vendas_db_credentials,
    kubernetes_secret_v1.vendas_mongo,
    kubernetes_secret_v1.vendas_cpf,
    kubernetes_secret_v1.vendas_tokens_servico,
  ]
}

output "comandos_uteis" {
  description = "Atalhos para a demonstracao."
  value = {
    pods        = "kubectl -n vendas get pods -o wide"
    mongosh     = "kubectl -n vendas exec -it statefulset/vendas-db -- sh -c 'mongosh -u \"$MONGO_APP_USUARIO\" -p \"$MONGO_APP_SENHA\" --authenticationDatabase \"$MONGO_APP_BANCO\" \"$MONGO_APP_BANCO\"'"
    compras     = "db.compras.find({}, {\"cpf.cifrado\": 0}).sort({criada_em: -1}).limit(5)"
    cronjob     = "kubectl -n vendas get cronjob,jobs"
    recuperacao = "kubectl -n vendas logs -l app=revenda-vendas-recuperacao --tail=50"
  }
}
