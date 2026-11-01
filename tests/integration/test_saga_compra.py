"""Saga da compra pela API, com o principal falso (docs/02 da solução, 2.2 e 2.8)."""

from __future__ import annotations

from datetime import timedelta

from apoio.api import Api, tipo_problema
from apoio.cenario import CPF_MASCARADO
from apoio.principal_falso import PrincipalFalso
from vendas.composicao import Componentes
from vendas.shared.clock import RelogioFixo


def test_caminho_feliz_compra_pagamento_e_efetivacao(api: Api, principal: PrincipalFalso) -> None:
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    assert compra["status"] == "AGUARDANDO_PAGAMENTO"
    assert compra["cpf"] == CPF_MASCARADO
    assert compra["preco"] == "119900.00"
    assert compra["codigo_pagamento"].startswith("PAG-")
    assert compra["expira_em"] == "2026-11-04T10:30:00Z"
    assert api.anuncio(veiculo)["status"] == "RESERVADO"
    assert api.a_venda()["total"] == 0
    assert principal.veiculo(veiculo).status == "BLOQUEADO"

    codigo = principal.pagar(compra["id"])
    resposta = api.resultado(compra["id"], codigo, "PAGO")
    assert resposta.status_code == 200
    efetivada = resposta.json()
    assert (efetivada["status"], efetivada["data_venda"]) == ("EFETIVADA", "2026-11-04T10:00:00Z")
    assert principal.veiculo(veiculo).status == "VENDIDO"  # T6
    vendidos = api.vendidos()["itens"]
    assert [(v["veiculo_id"], v["preco_venda"]) for v in vendidos] == [(veiculo, "119900.00")]
    assert "cpf" not in vendidos[0]
    # Repasse repetido pela outbox: 200 sem efeito.
    assert api.resultado(compra["id"], codigo, "PAGO").json()["status"] == "EFETIVADA"


def test_pagamento_cancelado_devolve_o_veiculo(api: Api, principal: PrincipalFalso) -> None:
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    cobranca = principal.cobrancas[compra["id"]]
    cobranca.status = "CANCELADA"  # o processador cancelou (webhook no principal)
    resposta = api.resultado(compra["id"], cobranca.codigo, "CANCELADO")
    corpo = resposta.json()
    assert (corpo["status"], corpo["motivo_cancelamento"]) == ("CANCELADA", "PAGAMENTO_CANCELADO")
    assert corpo["codigo_pagamento"] is None
    assert api.anuncio(veiculo)["status"] == "A_VENDA"
    assert principal.veiculo(veiculo).status == "A_VENDA"  # C2


def test_versao_desatualizada_e_recusada_sem_reservar(api: Api, principal: PrincipalFalso) -> None:
    veiculo = api.replicar()
    api.replicar(veiculo, versao=2, preco="99000.00")
    resposta = api.comprar(veiculo, versao=1)
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "anuncio-alterado"
    assert principal.chamadas == []


def test_gestor_editou_entre_a_replica_e_o_bloqueio(api: Api, principal: PrincipalFalso) -> None:
    """V5: a vitrine ainda mostra a versão 1, mas o catálogo já está na 2."""
    veiculo = api.replicar()
    principal.veiculo(veiculo).versao = 2
    resposta = api.comprar(veiculo)
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "anuncio-alterado"
    assert api.anuncio(veiculo)["status"] == "A_VENDA"  # C1
    assert principal.caminhos("bloqueio") == [
        f"/internal/v1/veiculos/{veiculo}/bloqueio",
        f"/internal/v1/veiculos/{veiculo}/bloqueio/" + principal.chamadas[-1][1].split("/")[-1],
    ]


def test_principal_fora_do_ar_compensa_e_responde_503(
    api: Api, principal: PrincipalFalso, componentes: Componentes
) -> None:
    """V4: falha em T2; a compra é desfeita e o veículo volta à vitrine na hora."""
    veiculo = api.replicar()
    principal.fora_do_ar = True
    resposta = api.comprar(veiculo)
    assert resposta.status_code == 503
    assert tipo_problema(resposta) == "servico-principal-indisponivel"
    assert resposta.headers["Retry-After"] == "15"
    assert api.anuncio(veiculo)["status"] == "A_VENDA"
    # A compensação remota (C2) ficou pendente e é concluída quando o principal volta.
    principal.fora_do_ar = False
    assert componentes.recuperar.executar().sagas_processadas == 0  # ainda no backoff


def test_principal_lento_e_fora_do_ar_uma_tentativa_de_compensacao_por_requisicao(
    api: Api, principal: PrincipalFalso, relogio: RelogioFixo
) -> None:
    """Cada chamada ao principal leva 2 s (timeout). Falhou a compensação remota, a saga
    espera a próxima tentativa agendada: não repete o C2 na mesma requisição, que ficaria
    lenta para o comprador (o caso visto no cluster: 3 C2 seguidos, 24 s de resposta)."""
    veiculo = api.replicar()
    principal.fora_do_ar = True
    principal.ao_chamar = lambda: relogio.avancar(timedelta(seconds=2))
    resposta = api.comprar(veiculo)
    assert resposta.status_code == 503
    assert api.anuncio(veiculo)["status"] == "A_VENDA"  # C1 local na hora
    tentativas_t2 = 3  # o cliente HTTP repete até 3 vezes
    tentativas_c2 = 3
    assert len(principal.caminhos("bloqueio")) == tentativas_t2 + tentativas_c2


def test_compensacao_pendente_concluida_pela_recuperacao(
    api: Api, principal: PrincipalFalso, componentes: Componentes, relogio: RelogioFixo
) -> None:
    veiculo = api.replicar()
    principal.falhas["cobrancas"] = [503, 503, 503, 503, 503, 503]  # T3 e C3 falham
    resposta = api.comprar(veiculo)
    assert resposta.status_code == 503
    assert principal.veiculo(veiculo).status == "A_VENDA"  # C2 conseguiu
    relogio.avancar(timedelta(minutes=1))
    assert componentes.recuperar.executar().sagas_processadas == 1
    compra_id = principal.caminhos("cancelamento")[-1].split("/")[-2]
    assert principal.cobrancas[compra_id].status == "CANCELADA"
    assert compra_id in principal.compensadas


def test_expiracao_pela_recuperacao(
    api: Api, principal: PrincipalFalso, componentes: Componentes, relogio: RelogioFixo
) -> None:
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    relogio.avancar(timedelta(minutes=31))
    assert componentes.recuperar.executar().sagas_processadas == 1
    corpo = api.compra(compra["id"])
    assert (corpo["status"], corpo["motivo_cancelamento"]) == ("CANCELADA", "RESERVA_EXPIRADA")
    assert principal.cobrancas[compra["id"]].status == "CANCELADA"
    assert api.anuncio(veiculo)["status"] == "A_VENDA"


def test_pagamento_venceu_a_expiracao(
    api: Api, principal: PrincipalFalso, componentes: Componentes, relogio: RelogioFixo
) -> None:
    """Pago no último instante e o repasse ainda não chegou: C3 avisa que o pivô ocorreu."""
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    principal.pagar(compra["id"])
    relogio.avancar(timedelta(minutes=31))
    componentes.recuperar.executar()
    assert api.compra(compra["id"])["status"] == "EFETIVADA"
    assert principal.veiculo(veiculo).status == "VENDIDO"


def test_nova_compra_expira_a_reserva_vencida_na_hora(
    api: Api, principal: PrincipalFalso, relogio: RelogioFixo
) -> None:
    veiculo = api.replicar()
    primeira = api.compra_ok(veiculo)
    relogio.avancar(timedelta(minutes=31))
    segunda = api.compra_ok(veiculo)
    assert api.compra(primeira["id"])["motivo_cancelamento"] == "RESERVA_EXPIRADA"
    assert segunda["status"] == "AGUARDANDO_PAGAMENTO"


def test_t6_adiado_e_concluido_pela_recuperacao(
    api: Api, principal: PrincipalFalso, componentes: Componentes, relogio: RelogioFixo
) -> None:
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    codigo = principal.pagar(compra["id"])
    principal.falhas["venda"] = [503, 503, 503]
    assert api.resultado(compra["id"], codigo, "PAGO").json()["status"] == "EFETIVADA"
    assert principal.veiculo(veiculo).status == "BLOQUEADO"  # T6 ainda pendente
    relogio.avancar(timedelta(minutes=1))
    componentes.recuperar.executar()
    assert principal.veiculo(veiculo).status == "VENDIDO"


def test_circuito_abre_e_falha_rapido(api: Api, principal: PrincipalFalso) -> None:
    principal.fora_do_ar = True
    for _ in range(3):  # limite de 3 falhas nos testes
        api.comprar(api.replicar())
    chamadas = len(principal.chamadas)
    resposta = api.comprar(api.replicar())
    assert resposta.status_code == 503
    assert len(principal.chamadas) == chamadas  # nenhuma chamada: circuito aberto
    metricas = api.http.get("/metrics").text
    assert 'revenda_circuito_estado{destino="principal"} 2.0' in metricas
