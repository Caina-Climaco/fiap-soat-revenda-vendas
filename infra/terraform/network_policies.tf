# NetworkPolicies de entrada do servico de venda. O CNI padrao do kind (kindnet) aplica
# NetworkPolicy desde o kind v0.24. Somente regras de ENTRADA: a saida (DNS, principal,
# Collector) nao e restringida.

locals {
  # Trafego do proprio no: probes do kubelet e NodePort (apos o SNAT do kube-proxy, a
  # origem e o gateway da faixa de pods ou um IP fora dela).
  origem_no = [
    { cidr = "0.0.0.0/0", except = [var.pod_subnet] },
    { cidr = "${cidrhost(var.pod_subnet, 1)}/32", except = [] },
  ]
}

# vendas-db: so a API e o CronJob de recuperacao, mais o host quando expor_banco = true.
resource "kubernetes_network_policy_v1" "vendas_db" {
  metadata {
    name      = "vendas-db-somente-servico"
    namespace = kubernetes_namespace_v1.vendas.metadata[0].name
    labels    = local.rotulos_comuns
  }

  spec {
    pod_selector {
      match_labels = {
        app = "vendas-db"
      }
    }

    policy_types = ["Ingress"]

    ingress {
      from {
        pod_selector {
          match_expressions {
            key      = "app"
            operator = "In"
            values   = ["revenda-vendas", "revenda-vendas-recuperacao"]
          }
        }
      }
      ports {
        port     = "27017"
        protocol = "TCP"
      }
    }

    dynamic "ingress" {
      for_each = var.expor_banco ? [1] : []
      content {
        dynamic "from" {
          for_each = local.origem_no
          content {
            ip_block {
              cidr   = from.value.cidr
              except = from.value.except
            }
          }
        }
        ports {
          port     = "27017"
          protocol = "TCP"
        }
      }
    }
  }
}

# revenda-vendas: dentro do cluster, so chegam aqui
#   - o Kong (namespace gateway): vitrine e compras;
#   - o principal (namespace principal): outbox (replica do anuncio e resultado do
#     pagamento), que alem disso apresenta o X-Service-Token;
#   - o Prometheus (namespace observabilidade): /metrics.
resource "kubernetes_network_policy_v1" "revenda_vendas" {
  metadata {
    name      = "revenda-vendas-entrada"
    namespace = kubernetes_namespace_v1.vendas.metadata[0].name
    labels    = local.rotulos_comuns
  }

  spec {
    pod_selector {
      match_labels = {
        app = "revenda-vendas"
      }
    }

    policy_types = ["Ingress"]

    ingress {
      from {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = "gateway"
          }
        }
        pod_selector {
          match_labels = {
            app = "kong"
          }
        }
      }
      from {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = var.namespace_principal
          }
        }
        pod_selector {
          match_labels = {
            app = "revenda-principal"
          }
        }
      }
      from {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = "observabilidade"
          }
        }
        pod_selector {
          match_labels = {
            app = "prometheus"
          }
        }
      }
      ports {
        port     = "8000"
        protocol = "TCP"
      }
    }

    ingress {
      dynamic "from" {
        for_each = local.origem_no
        content {
          ip_block {
            cidr   = from.value.cidr
            except = from.value.except
          }
        }
      }
      ports {
        port     = "8000"
        protocol = "TCP"
      }
    }
  }
}
