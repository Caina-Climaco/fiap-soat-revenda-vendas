"""Configuração por variáveis de ambiente."""

from __future__ import annotations

import base64
import binascii
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MongoSettings(BaseSettings):
    """Só o necessário para conectar ao MongoDB (usada também pelo CronJob)."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    mongo_url: SecretStr = SecretStr("mongodb://localhost:27017")
    mongo_banco: str = "revenda_vendas"


class Settings(MongoSettings):
    principal_url: str = "http://revenda-principal.principal.svc.cluster.local:8000"
    # Token que o principal apresenta ao chamar as rotas internas deste serviço.
    token_servico_entrada: SecretStr = Field(min_length=16)
    # Token que este serviço apresenta ao chamar as rotas internas do principal.
    token_servico_saida: SecretStr = Field(min_length=16)
    # Chave AES-256 do CPF em base64 (32 bytes) e o identificador gravado com a cifra.
    cpf_chave: SecretStr
    cpf_chave_id: str = Field(default="k1", pattern=r"^[A-Za-z0-9_-]{1,16}$")

    prazo_reserva_minutos: int = Field(default=30, ge=1, le=24 * 60)
    retencao_cpf_dias: int = Field(default=90, ge=1, le=3650)

    http_timeout_segundos: float = Field(default=2.0, gt=0, le=30)
    http_tentativas: int = Field(default=3, ge=1, le=10)
    circuito_limite_falhas: int = Field(default=5, ge=1, le=100)
    circuito_aberto_segundos: float = Field(default=15.0, gt=0, le=600)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # Prefixo do Swagger e do openapi.json. No cluster é "/vendas": o Kong publica a
    # documentação dos dois serviços na mesma porta.
    docs_prefixo: str = Field(default="", pattern=r"^(/[a-z0-9-]+)*$")

    @field_validator("principal_url")
    @classmethod
    def _sem_barra_final(cls, valor: str) -> str:
        return valor.rstrip("/")

    @field_validator("log_level", mode="before")
    @classmethod
    def _maiusculas(cls, valor: object) -> object:
        return valor.upper() if isinstance(valor, str) else valor

    @field_validator("cpf_chave")
    @classmethod
    def _chave_aes_256(cls, valor: SecretStr) -> SecretStr:
        try:
            bruta = base64.b64decode(valor.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("CPF_CHAVE deve estar em base64") from exc
        if len(bruta) != 32:
            raise ValueError("CPF_CHAVE deve ter 32 bytes (AES-256)")
        return valor

    @property
    def chave_cpf_bytes(self) -> bytes:
        return base64.b64decode(self.cpf_chave.get_secret_value())
