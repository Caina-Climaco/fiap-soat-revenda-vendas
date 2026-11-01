# MongoDB 7 do servico de venda (ADR-001 deste servico): vendas-db, no namespace vendas,
# isolado do PostgreSQL do principal. StatefulSet + PVC (storage class "standard" do kind)
# + Service. Sem replica set: cada passo da saga e atomico num documento, sem transacao.
#
# Na primeira subida (volume vazio) o entrypoint oficial cria o usuario root e roda o
# script de inicializacao abaixo, que cria o usuario da aplicacao so com readWrite no
# banco revenda_vendas. A API e o CronJob usam esse usuario, nunca o root.

locals {
  mongo_rotulos = merge(local.rotulos_comuns, {
    app                           = "vendas-db"
    "app.kubernetes.io/name"      = "mongodb"
    "app.kubernetes.io/instance"  = "vendas-db"
    "app.kubernetes.io/component" = "database"
  })

  mongo_init = <<-JS
    // Usuario da aplicacao: so leitura e escrita (e indices) no banco do servico de venda
    const banco = process.env.MONGO_APP_BANCO;
    db.getSiblingDB(banco).createUser({
      user: process.env.MONGO_APP_USUARIO,
      pwd: process.env.MONGO_APP_SENHA,
      roles: [{ role: "readWrite", db: banco }],
    });
  JS
}

resource "kubernetes_config_map_v1" "mongo_init" {
  metadata {
    name      = "vendas-db-init"
    namespace = kubernetes_namespace_v1.vendas.metadata[0].name
    labels    = local.rotulos_comuns
  }
  data = {
    "01-usuario-aplicacao.js" = local.mongo_init
  }
}

resource "kubernetes_stateful_set_v1" "mongo" {
  metadata {
    name      = "vendas-db"
    namespace = kubernetes_namespace_v1.vendas.metadata[0].name
    labels    = local.mongo_rotulos
  }

  spec {
    service_name = "vendas-db"
    replicas     = 1

    selector {
      match_labels = {
        app = "vendas-db"
      }
    }

    template {
      metadata {
        labels = local.mongo_rotulos
        annotations = {
          "revenda.io/init-sha256" = sha256(local.mongo_init)
        }
      }

      spec {
        automount_service_account_token  = false
        enable_service_links             = false
        termination_grace_period_seconds = 30

        # UID/GID 999 = usuario "mongodb" da imagem oficial
        security_context {
          run_as_non_root = true
          run_as_user     = 999
          run_as_group    = 999
          fs_group        = 999
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name              = "mongodb"
          image             = var.mongo_imagem
          image_pull_policy = "IfNotPresent"
          args              = ["--wiredTigerCacheSizeGB", tostring(var.mongo_cache_gb)]

          port {
            name           = "mongodb"
            container_port = 27017
            protocol       = "TCP"
          }

          # MONGO_INITDB_ROOT_*, MONGO_APP_USUARIO, MONGO_APP_SENHA, MONGO_APP_BANCO
          env_from {
            secret_ref {
              name = kubernetes_secret_v1.vendas_db_credentials.metadata[0].name
            }
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "256Mi"
            }
            limits = {
              cpu    = "1"
              memory = "768Mi"
            }
          }

          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            capabilities {
              drop = ["ALL"]
            }
          }

          # Na inicializacao o entrypoint sobe um mongod temporario so em 127.0.0.1; a porta
          # no IP do pod so abre com o servidor definitivo.
          startup_probe {
            tcp_socket {
              port = "mongodb"
            }
            period_seconds    = 5
            timeout_seconds   = 3
            failure_threshold = 60
          }
          readiness_probe {
            tcp_socket {
              port = "mongodb"
            }
            period_seconds    = 5
            timeout_seconds   = 3
            failure_threshold = 3
          }
          liveness_probe {
            tcp_socket {
              port = "mongodb"
            }
            period_seconds    = 15
            timeout_seconds   = 3
            failure_threshold = 6
          }

          volume_mount {
            name       = "dados"
            mount_path = "/data/db"
          }
          volume_mount {
            name       = "configdb"
            mount_path = "/data/configdb"
          }
          volume_mount {
            name       = "init"
            mount_path = "/docker-entrypoint-initdb.d"
            read_only  = true
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
        }

        volume {
          name = "configdb"
          empty_dir {}
        }
        volume {
          name = "init"
          config_map {
            name = kubernetes_config_map_v1.mongo_init.metadata[0].name
          }
        }
        volume {
          name = "tmp"
          empty_dir {}
        }
      }
    }

    volume_claim_template {
      metadata {
        name = "dados"
      }
      spec {
        access_modes       = ["ReadWriteOnce"]
        storage_class_name = "standard"
        resources {
          requests = {
            storage = "1Gi"
          }
        }
      }
    }
  }

  wait_for_rollout = true

  timeouts {
    create = "10m"
    update = "10m"
  }
}

resource "kubernetes_service_v1" "mongo" {
  metadata {
    name      = "vendas-db"
    namespace = kubernetes_namespace_v1.vendas.metadata[0].name
    labels    = local.mongo_rotulos
  }

  spec {
    type = var.expor_banco ? "NodePort" : "ClusterIP"
    selector = {
      app = "vendas-db"
    }
    port {
      name        = "mongodb"
      port        = 27017
      target_port = "mongodb"
      protocol    = "TCP"
      node_port   = var.expor_banco ? 30017 : null
    }
  }
}
