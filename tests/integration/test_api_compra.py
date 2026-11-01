"""Compra pela API: validação, CPF (LGPD), concorrência e consulta."""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI

from apoio.api import Api, tipo_problema
from apoio.cenario import CPF, CPF_DIGITOS, CPF_MASCARADO
from apoio.principal_falso import PrincipalFalso
from vendas.shared.logging import FormatadorJson
from vendas.shared.mongo import BancoMongo


def test_location_e_consulta_da_compra(api: Api) -> None:
    resposta = api.comprar(api.replicar())
    assert resposta.status_code == 201
    compra = resposta.json()
    assert resposta.headers["Location"] == f"/api/v1/compras/{compra['id']}"
    assert api.compra(compra["id"]) == compra
    assert compra["veiculo"] == {
        "marca": "Toyota",
        "modelo": "Corolla XEi",
        "ano": 2022,
        "cor": "Prata",
    }


def test_cpf_cifrado_no_banco_e_mascarado_na_resposta(api: Api, banco: BancoMongo) -> None:
    compra = api.compra_ok(api.replicar())
    documento = banco.db.compras.find_one({"_id": compra["id"]})
    assert documento is not None
    assert documento["cpf"]["mascarado"] == CPF_MASCARADO
    assert documento["cpf"]["cifrado"].startswith("k1:")
    assert CPF_DIGITOS not in json.dumps(documento, default=str)
    assert CPF not in json.dumps(documento, default=str)


@pytest.mark.parametrize("cpf", ["529.982.247-24", "111.111.111-11", "52998224"])
def test_cpf_invalido(api: Api, cpf: str) -> None:
    resposta = api.comprar(api.replicar(), cpf=cpf)
    assert resposta.status_code == 422
    assert tipo_problema(resposta) in {"cpf-invalido", "validacao"}


def test_payload_invalido(api: Api) -> None:
    veiculo = api.replicar()
    for corpo in (
        {"veiculo_id": veiculo, "versao": "1", "cpf": CPF},
        {"veiculo_id": veiculo, "cpf": CPF},
        {"veiculo_id": "x", "versao": 1, "cpf": CPF},
        {"veiculo_id": veiculo, "versao": 1, "cpf": CPF, "nome": "Fulano"},
    ):
        assert api.http.post("/api/v1/compras", json=corpo).status_code == 422


def test_veiculo_fora_da_vitrine(api: Api) -> None:
    resposta = api.comprar(str(uuid.uuid4()))
    assert resposta.status_code == 404
    assert tipo_problema(resposta) == "anuncio-nao-encontrado"


def test_segunda_compra_do_mesmo_veiculo(api: Api) -> None:
    veiculo = api.replicar()
    api.compra_ok(veiculo)
    resposta = api.comprar(veiculo)
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "veiculo-indisponivel"


def test_veiculo_bloqueado_por_outra_compra_no_catalogo(
    api: Api, principal: PrincipalFalso
) -> None:
    veiculo = api.replicar()
    principal.veiculo(veiculo).status = "BLOQUEADO"
    resposta = api.comprar(veiculo)
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "veiculo-indisponivel"
    assert api.anuncio(veiculo)["status"] == "A_VENDA"


def test_indice_unico_parcial_barra_duas_compras_ativas(api: Api, banco: BancoMongo) -> None:
    """RN-03 garantido pelo banco, mesmo se a checagem da aplicação for contornada."""
    veiculo = api.replicar()
    api.compra_ok(veiculo)
    from pymongo.errors import DuplicateKeyError

    with pytest.raises(DuplicateKeyError):
        banco.db.compras.insert_one({"_id": "outra", "veiculo_id": veiculo, "ativa": True})
    banco.db.compras.insert_one({"_id": "inativa", "veiculo_id": veiculo, "ativa": False})


def test_compra_inexistente(api: Api) -> None:
    resposta = api.http.get(f"/api/v1/compras/{uuid.uuid4()}")
    assert resposta.status_code == 404
    assert tipo_problema(resposta) == "compra-nao-encontrada"


def test_resultado_para_compra_desconhecida_e_divergente(
    api: Api, principal: PrincipalFalso
) -> None:
    desconhecida = api.resultado(str(uuid.uuid4()), "PAG-000000000001", "PAGO")
    assert desconhecida.status_code == 404  # a outbox tenta de novo
    compra = api.compra_ok(api.replicar())
    divergente = api.resultado(compra["id"], "PAG-999999999999", "PAGO")
    assert divergente.status_code == 409
    assert tipo_problema(divergente) == "resultado-divergente"


@pytest.fixture
def logs(app: FastAPI) -> Iterator[io.StringIO]:
    saida = io.StringIO()
    handler = logging.StreamHandler(saida)
    handler.setFormatter(FormatadorJson())
    raiz = logging.getLogger()
    raiz.addHandler(handler)
    yield saida
    raiz.removeHandler(handler)


def test_cpf_nunca_aparece_nos_logs(api: Api, logs: io.StringIO, principal: PrincipalFalso) -> None:
    compra = api.compra_ok(api.replicar())
    api.resultado(compra["id"], principal.pagar(compra["id"]), "PAGO")
    texto = logs.getvalue()
    assert texto
    assert CPF_DIGITOS not in texto
    assert CPF not in texto
    eventos = {json.loads(linha).get("evento") for linha in texto.splitlines() if "evento" in linha}
    assert {"CompraIniciada", "CompraAguardandoPagamento", "CompraEfetivada"} <= eventos
