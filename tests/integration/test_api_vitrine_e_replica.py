"""Réplica dos anúncios (ADR-003 do serviço de venda) e vitrine pública (RF-03, RF-04)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from apoio.api import SERVICO, Api, tipo_problema
from apoio.cenario import ANUNCIO
from apoio.principal_falso import PrincipalFalso


def test_replica_so_aplica_versao_mais_nova(api: Api) -> None:
    veiculo = str(uuid.uuid4())
    caminho = f"/internal/v1/anuncios/{veiculo}"
    assert api.http.put(caminho, json=ANUNCIO, headers=SERVICO).json() == {"aplicado": True}
    assert api.http.put(caminho, json=ANUNCIO, headers=SERVICO).json() == {"aplicado": False}
    nova = {**ANUNCIO, "versao": 3, "preco": "100000.00"}
    assert api.http.put(caminho, json=nova, headers=SERVICO).json() == {"aplicado": True}
    antiga = {**ANUNCIO, "versao": 2, "preco": "1.00"}
    assert api.http.put(caminho, json=antiga, headers=SERVICO).json() == {"aplicado": False}
    anuncio = api.anuncio(veiculo)
    assert (anuncio["versao"], anuncio["preco"], anuncio["status"]) == (3, "100000.00", "A_VENDA")


def test_replica_nao_mexe_na_disponibilidade(api: Api) -> None:
    veiculo = api.replicar()
    api.compra_ok(veiculo)
    api.replicar(veiculo, versao=2, cor="Preto")
    anuncio = api.anuncio(veiculo)
    assert (anuncio["status"], anuncio["cor"]) == ("RESERVADO", "Preto")


@pytest.mark.parametrize("token", [None, "errado"])
def test_rotas_internas_exigem_token(cliente_http: TestClient, token: str | None) -> None:
    cabecalhos = {} if token is None else {"X-Service-Token": token}
    compra = uuid.uuid4()
    for metodo, caminho, corpo in [
        ("PUT", f"/internal/v1/anuncios/{uuid.uuid4()}", ANUNCIO),
        (
            "POST",
            f"/internal/v1/compras/{compra}/resultado-pagamento",
            {"codigo_pagamento": "PAG-000000000001", "status": "PAGO"},
        ),
    ]:
        resposta = cliente_http.request(metodo, caminho, json=corpo, headers=cabecalhos)
        assert resposta.status_code == 401
        assert tipo_problema(resposta) == "servico-nao-autorizado"


@pytest.mark.parametrize(
    "campos", [{"versao": 0}, {"versao": "1"}, {"preco": "0"}, {"ano": 1800}, {"extra": 1}]
)
def test_replica_invalida(api: Api, campos: dict[str, object]) -> None:
    resposta = api.http.put(
        f"/internal/v1/anuncios/{uuid.uuid4()}", json={**ANUNCIO, **campos}, headers=SERVICO
    )
    assert resposta.status_code == 422


def test_vitrine_ordenada_por_preco_com_desempate_e_paginada(api: Api) -> None:
    caro = api.replicar(preco="90000.00")
    barato = api.replicar(preco="50000.00")
    medio_a = api.replicar(preco="70000.00", criado_em="2026-11-01T00:00:00+00:00")
    medio_b = api.replicar(preco="70000.00", criado_em="2026-11-02T00:00:00+00:00")
    pagina = api.a_venda()
    assert [i["veiculo_id"] for i in pagina["itens"]] == [barato, medio_a, medio_b, caro]
    assert pagina["itens"][0] == {
        "veiculo_id": barato,
        "marca": "Toyota",
        "modelo": "Corolla XEi",
        "ano": 2022,
        "cor": "Prata",
        "preco": "50000.00",
        "versao": 1,
    }
    segunda = api.a_venda(limite=2, deslocamento=2)
    assert [i["veiculo_id"] for i in segunda["itens"]] == [medio_b, caro]
    assert (segunda["total"], segunda["limite"], segunda["deslocamento"]) == (4, 2, 2)


def test_vendidos_ordenados_pelo_preco_da_venda(api: Api, principal: PrincipalFalso) -> None:
    precos = {
        "80000.00": api.replicar(preco="80000.00"),
        "60000.00": api.replicar(preco="60000.00"),
    }
    for veiculo in precos.values():
        compra = api.compra_ok(veiculo)
        api.resultado(compra["id"], principal.pagar(compra["id"]), "PAGO")
    vendidos = api.vendidos()
    assert [i["preco_venda"] for i in vendidos["itens"]] == ["60000.00", "80000.00"]
    assert vendidos["itens"][0]["data_venda"] == "2026-11-04T10:00:00Z"
    assert api.a_venda()["total"] == 0


def test_vitrine_funciona_com_o_principal_fora_do_ar(api: Api, principal: PrincipalFalso) -> None:
    """RN-22: o isolamento do serviço de venda."""
    api.replicar()
    principal.fora_do_ar = True
    assert api.a_venda()["total"] == 1
    assert principal.chamadas == []


def test_paginacao_invalida_e_anuncio_inexistente(api: Api) -> None:
    assert api.http.get("/api/v1/veiculos/a-venda", params={"limite": 0}).status_code == 422
    assert api.http.get("/api/v1/veiculos/vendidos", params={"limite": 101}).status_code == 422
    resposta = api.http.get(f"/api/v1/anuncios/{uuid.uuid4()}")
    assert resposta.status_code == 404
    assert tipo_problema(resposta) == "anuncio-nao-encontrado"
