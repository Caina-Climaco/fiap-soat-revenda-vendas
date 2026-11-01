# Runner self-hosted do GitHub Actions para o CD local (ADR-006), em container Linux no
# Docker Desktop do PC: no Windows o Smart App Control bloqueia as DLLs do runner.
# Base oficial (Ubuntu 24.04, usuario runner UID 1001 com sudo, docker CLI + buildx,
# runner em /home/runner). Ferramentas extras com versao fixa e checksum verificado.
# Build (no PC): docker build -t revenda-runner:2.337.0 infra/runner (mesma imagem nos dois repositorios)
# Execucao: scripts/windows/03-instalar-runner.ps1 (docker run na rede "kind").
ARG RUNNER_VERSION=2.337.0
FROM ghcr.io/actions/actions-runner:${RUNNER_VERSION}

# kind: mesma versao da CLI do PC (infra/kind/cluster.yaml usa imagem do release v0.33.0)
ARG KIND_VERSION=v0.33.0
ARG KIND_SHA256=aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d
# kubectl: SHA-512 do kubernetes-client-linux-amd64.tar.gz publicado no CHANGELOG-1.34.md
ARG KUBECTL_VERSION=v1.34.12
ARG KUBECTL_CLIENT_SHA512=46596080fe335de00bf3eab6fb02ec737a75133bfdabd9191381fc3a077c6e4eb3eeff8c434f6f8cc0ec3690e22a19410a1c875a32f83f5091e96b860c5f6f40
# Terraform: mesma versao do PC (o state e compartilhado com o 04-subir-ambiente.ps1).
# O zip e conferido pelo SHA256SUMS, cuja assinatura GPG e conferida pela chave da HashiCorp.
ARG TERRAFORM_VERSION=1.16.4
ARG HASHICORP_GPG_FINGERPRINT=C874011F0AB405110D02105534365D9472D7468F

USER 0
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# python3 (3.12 no Ubuntu 24.04) + venv + pip para o e2e; gnupg para conferir o Terraform.
# curl, jq, git, unzip e sudo ja vem na imagem base. Pacotes apt sem versao fixa: a
# reprodutibilidade vem da tag da imagem base.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-venv python3-pip gnupg ca-certificates curl jq git unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/ferramentas

RUN curl -fsSLo kind "https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/kind-linux-amd64" \
    && echo "${KIND_SHA256}  kind" | sha256sum -c - \
    && install -o root -g root -m 0755 kind /usr/local/bin/kind \
    && rm -f kind

RUN curl -fsSLo cliente.tar.gz "https://dl.k8s.io/${KUBECTL_VERSION}/kubernetes-client-linux-amd64.tar.gz" \
    && echo "${KUBECTL_CLIENT_SHA512}  cliente.tar.gz" | sha512sum -c - \
    && tar -xzf cliente.tar.gz kubernetes/client/bin/kubectl \
    && install -o root -g root -m 0755 kubernetes/client/bin/kubectl /usr/local/bin/kubectl \
    && rm -rf cliente.tar.gz kubernetes

RUN base="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}" \
    && curl -fsSLo terraform.zip "${base}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
    && curl -fsSLo SHA256SUMS "${base}/terraform_${TERRAFORM_VERSION}_SHA256SUMS" \
    && curl -fsSLo SHA256SUMS.sig "${base}/terraform_${TERRAFORM_VERSION}_SHA256SUMS.sig" \
    && curl -fsSLo hashicorp.asc https://www.hashicorp.com/.well-known/pgp-key.txt \
    && export GNUPGHOME="/tmp/ferramentas/gnupg" \
    && mkdir -m 0700 "$GNUPGHOME" \
    && gpg --batch --quiet --import hashicorp.asc \
    && gpg --batch --list-keys --with-colons | grep -q "^fpr:::::::::${HASHICORP_GPG_FINGERPRINT}:" \
    && gpg --batch --verify SHA256SUMS.sig SHA256SUMS \
    && grep " terraform_${TERRAFORM_VERSION}_linux_amd64.zip\$" SHA256SUMS | sed "s/terraform_${TERRAFORM_VERSION}_linux_amd64.zip/terraform.zip/" | sha256sum -c - \
    && unzip -q terraform.zip terraform \
    && install -o root -g root -m 0755 terraform /usr/local/bin/terraform \
    && rm -rf /tmp/ferramentas/*

# Volume persistente (config do registro e TF_DATA_DIR). Criado aqui com dono runner para
# que um volume nomeado novo herde a permissao. /revenda-state e o bind mount do diretorio
# do state no Windows (%USERPROFILE%\.revenda).
RUN mkdir -p /home/runner/persist /revenda-state \
    && chown runner:docker /home/runner/persist

COPY --chown=root:root --chmod=0755 entrypoint.sh /usr/local/bin/revenda-runner-entrypoint

WORKDIR /home/runner
# UID 1001 = usuario runner da imagem base (grupos sudo e docker)
USER 1001
ENV TF_IN_AUTOMATION=1 \
    TF_INPUT=0 \
    TF_DATA_DIR=/home/runner/persist/terraform-data \
    KUBECONFIG=/home/runner/.kube/config

ENTRYPOINT ["/usr/local/bin/revenda-runner-entrypoint"]
