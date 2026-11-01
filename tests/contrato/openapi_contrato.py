"""Contratos das rotas internas entre os dois servicos (ADR-008).

O mesmo modulo existe nos dois repositorios. Um contrato e o recorte `/internal/v1` da
OpenAPI de um servico (com os esquemas que ele referencia), versionado em `contratos/`:

- provedor: o recorte gerado da aplicacao real tem de ser igual ao arquivo publicado
  (quem muda uma rota interna atualiza o contrato no mesmo PR, com `python -m
  contrato.gerar`, e o outro repositorio e avisado pelo job `contrato` dele);
- consumidor: toda requisicao que o servico faz ao outro, e toda resposta que os testes
  simulam do outro, e validada contra a copia do contrato do outro.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

PREFIXO_INTERNO = "/internal/v1"
DIRETORIO = Path(__file__).resolve().parents[2] / "contratos"
_REF = re.compile(r'"\$ref":\s*"#/components/schemas/([^"]+)"')


def recortar(openapi: Mapping[str, Any], titulo: str) -> dict[str, Any]:
    """Rotas internas e o fecho dos esquemas que elas referenciam, em forma estavel."""
    caminhos = {
        caminho: operacoes
        for caminho, operacoes in openapi["paths"].items()
        if caminho.startswith(PREFIXO_INTERNO)
    }
    todos = openapi.get("components", {}).get("schemas", {})
    usados: set[str] = set()
    pendentes = set(_REF.findall(json.dumps(caminhos)))
    while pendentes:
        nome = pendentes.pop()
        if nome in usados:
            continue
        usados.add(nome)
        pendentes |= set(_REF.findall(json.dumps(todos[nome]))) - usados
    seguranca = openapi.get("components", {}).get("securitySchemes", {})
    return {
        "openapi": openapi["openapi"],
        "info": {"title": titulo, "version": "1"},
        "paths": caminhos,
        "components": {
            "schemas": {nome: todos[nome] for nome in sorted(usados)},
            "securitySchemes": {
                nome: esquema
                for nome, esquema in seguranca.items()
                if esquema.get("name") == "X-Service-Token"
            },
        },
    }


def serializar(contrato: Mapping[str, Any]) -> str:
    return json.dumps(contrato, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def carregar(nome: str) -> dict[str, Any]:
    dados: dict[str, Any] = json.loads((DIRETORIO / nome).read_text(encoding="utf-8"))
    return dados


class ViolacaoDeContratoError(AssertionError):
    pass


@dataclass(frozen=True)
class Operacao:
    modelo: str
    metodo: str
    definicao: dict[str, Any]


class Contrato:
    """Valida requisicoes e respostas reais contra um contrato carregado."""

    def __init__(self, documento: Mapping[str, Any]) -> None:
        self.documento = dict(documento)
        self._rotas = [
            (re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", modelo) + "$"), modelo)
            for modelo in self.documento["paths"]
        ]

    def operacao(self, metodo: str, caminho: str) -> Operacao:
        for padrao, modelo in self._rotas:
            if padrao.match(caminho):
                definicao = self.documento["paths"][modelo].get(metodo.lower())
                if definicao is None:
                    raise ViolacaoDeContratoError(f"{metodo} nao existe em {modelo}")
                return Operacao(modelo, metodo.upper(), definicao)
        raise ViolacaoDeContratoError(f"rota fora do contrato: {metodo} {caminho}")

    def validar_requisicao(self, metodo: str, caminho: str, corpo: Any) -> Operacao:
        operacao = self.operacao(metodo, caminho)
        definicao = operacao.definicao.get("requestBody")
        if definicao is None:
            if corpo not in (None, {}, b""):
                raise ViolacaoDeContratoError(f"{operacao.modelo} nao aceita corpo")
            return operacao
        esquema = definicao["content"]["application/json"]["schema"]
        self._validar(esquema, corpo, f"requisicao {metodo} {operacao.modelo}")
        return operacao

    def validar_resposta(self, metodo: str, caminho: str, status: int, corpo: Any) -> None:
        operacao = self.operacao(metodo, caminho)
        respostas = operacao.definicao.get("responses", {})
        definicao = respostas.get(str(status))
        if definicao is None:
            raise ViolacaoDeContratoError(
                f"status {status} nao documentado em {metodo} {operacao.modelo}"
            )
        conteudo = definicao.get("content", {})
        midia = conteudo.get("application/json") or conteudo.get("application/problem+json")
        if midia and "schema" in midia:
            self._validar(midia["schema"], corpo, f"resposta {status} {metodo} {operacao.modelo}")

    def _validar(self, esquema: Mapping[str, Any], instancia: Any, onde: str) -> None:
        # O documento inteiro e a raiz, para os "$ref": "#/components/..." resolverem.
        raiz = copy.deepcopy(self.documento)
        raiz.update(esquema)
        erros = sorted(Draft202012Validator(raiz).iter_errors(instancia), key=str)
        if erros:
            detalhe = "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in erros[:5])
            raise ViolacaoDeContratoError(f"{onde} fora do contrato: {detalhe}")
