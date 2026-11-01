# Imagem do serviço de venda (revenda-vendas).
# - A mesma imagem serve a API (CMD padrão) e o CronJob de recuperação de sagas
#   (`python -m vendas.recuperacao`). Sem migrações: os índices do MongoDB são criados
#   pela própria aplicação na subida (idempotente).
# - Usuário não-root 10001:10001; nada é escrito no FS raiz em execução (bytecode
#   compilado no build e PYTHONDONTWRITEBYTECODE=1), então funciona com
#   readOnlyRootFilesystem e um emptyDir em /tmp.
# - Sem dependências de desenvolvimento (uv sync --no-dev) e sem o código-fonte solto: o
#   pacote `vendas` é instalado no venv como wheel (--no-editable).

ARG PYTHON_IMAGE=python:3.12.12-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.32

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------- construção
FROM ${PYTHON_IMAGE} AS construcao

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# 1) Só as dependências (camada reaproveitada enquanto uv.lock não mudar).
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 2) O pacote da aplicação, instalado como wheel (não editável).
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------- execução
FROM ${PYTHON_IMAGE} AS execucao

LABEL org.opencontainers.image.title="revenda-vendas" \
      org.opencontainers.image.description="Serviço de venda da revenda de veículos: vitrine, compra e saga (FIAP PósTech SOAT, Fase 4)"

# Correções de segurança do SO publicadas depois da imagem base (o CI barra CVEs
# CRITICAL/HIGH com correção disponível) e o usuário sem privilégios da aplicação.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 revenda \
    && useradd --system --uid 10001 --gid 10001 --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin revenda

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    TMPDIR=/tmp

WORKDIR /app

# Arquivos pertencem ao root e são só lidos pelo usuário 10001.
COPY --from=construcao /app/.venv /app/.venv

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "vendas.main:app", "--host", "0.0.0.0", "--port", "8000"]
