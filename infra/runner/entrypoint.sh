#!/usr/bin/env bash
# Entrypoint do runner self-hosted em container (ADR-006).
#  1. Garante acesso ao /var/run/docker.sock para o usuario runner (sem rodar como root):
#     descobre o GID do socket, poe o runner nesse grupo e se reexecuta com o grupo ativo.
#  2. Primeira execucao: registra o runner (config.sh) com RUNNER_REPO_URL, RUNNER_TOKEN e
#     RUNNER_NAME e guarda a configuracao no volume /home/runner/persist.
#     Execucoes seguintes: restaura a configuracao do volume (o token nao e mais usado).
#  3. Executa ./run.sh (o runner se atualiza sozinho dentro do container).
# O volume NAO e montado em /home/runner inteiro, para nao esconder os binarios do runner.
set -euo pipefail

RUNNER_HOME=/home/runner
PERSIST="${RUNNER_HOME}/persist"
SOCKET=/var/run/docker.sock
ARQUIVOS_CONFIG=(.runner .credentials .credentials_rsaparams)

log() { printf '[revenda-runner] %s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
    log "ERRO: nao execute este container como root."
    exit 1
fi

# ---------------------------------------------------------------- acesso ao Docker
if [ ! -S "$SOCKET" ]; then
    log "ERRO: $SOCKET nao montado (docker run -v /var/run/docker.sock:/var/run/docker.sock)."
    exit 1
fi
if ! docker version >/dev/null 2>&1; then
    if [ "${REVENDA_REEXEC:-0}" = "1" ]; then
        log "ERRO: sem acesso a $SOCKET mesmo apos ajustar o grupo."
        ls -ln "$SOCKET"
        exit 1
    fi
    gid="$(stat -c %g "$SOCKET")"
    grupo="$(getent group "$gid" | cut -d: -f1 || true)"
    if [ -z "$grupo" ]; then
        grupo=docker-host
        sudo groupadd --gid "$gid" "$grupo"
    fi
    log "Socket do Docker com GID ${gid} (${grupo}): adicionando o usuario runner ao grupo."
    sudo usermod -aG "$grupo" runner
    # Novo processo como runner, agora com o grupo suplementar (sudo -E preserva o ambiente)
    exec sudo -E -H -u runner REVENDA_REEXEC=1 "$0" "$@"
fi

# ---------------------------------------------------------------- registro
cd "$RUNNER_HOME"
mkdir -p "$PERSIST"

if [ -f "${PERSIST}/.runner" ]; then
    log "Restaurando a configuracao do runner do volume persistente."
    for arquivo in "${ARQUIVOS_CONFIG[@]}"; do
        if [ -f "${PERSIST}/${arquivo}" ]; then
            cp -f "${PERSIST}/${arquivo}" "${RUNNER_HOME}/${arquivo}"
        fi
    done
else
    : "${RUNNER_REPO_URL:?defina RUNNER_REPO_URL (ex.: https://github.com/dono/repositorio)}"
    : "${RUNNER_TOKEN:?defina RUNNER_TOKEN (token de registro; gh api .../actions/runners/registration-token)}"
    : "${RUNNER_NAME:=$(hostname)-kind}"
    log "Registrando o runner ${RUNNER_NAME} em ${RUNNER_REPO_URL} (labels: ${RUNNER_LABELS:-kind-local})."
    ./config.sh --unattended \
        --url "$RUNNER_REPO_URL" \
        --token "$RUNNER_TOKEN" \
        --labels "${RUNNER_LABELS:-kind-local}" \
        --name "$RUNNER_NAME" \
        --work _work \
        --replace
    for arquivo in "${ARQUIVOS_CONFIG[@]}"; do
        if [ -f "${RUNNER_HOME}/${arquivo}" ]; then
            cp -f "${RUNNER_HOME}/${arquivo}" "${PERSIST}/${arquivo}"
            chmod 600 "${PERSIST}/${arquivo}"
        fi
    done
    log "Configuracao salva em ${PERSIST}."
fi
# O token de registro so serve uma vez e nao deve ficar visivel para os jobs
unset RUNNER_TOKEN

# ---------------------------------------------------------------- diagnostico rapido
if [ ! -w /revenda-state ]; then
    log "AVISO: /revenda-state ausente ou sem escrita (bind mount do %USERPROFILE%\\.revenda); o CD vai falhar no Terraform."
fi
mkdir -p "${TF_DATA_DIR:-${PERSIST}/terraform-data}"

log "Iniciando o runner."
exec ./run.sh "$@"
