"""Caminhos de borda da saga: conflito de revisão, recusas e anomalias depois do pivô."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from apoio.api import Api, tipo_problema
from apoio.principal_falso import PrincipalFalso
from vendas.composicao import Componentes
from vendas.domain.compra import Compra, ResultadoPagamento
from vendas.domain.repositorio import ConflitoRevisaoError, ResultadoReserva
from vendas.shared.clock import RelogioFixo
from vendas.shared.mongo import BancoMongo


def test_conflito_de_revisao_no_meio_da_saga_rele_e_continua(
    api: Api, componentes: Componentes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O repasse do pagamento grava no documento enquanto a saga anda: ela relê e segue."""
    repo = componentes.orquestrador._compras
    original = repo.salvar
    conflitos = iter([True])

    def salvar_com_um_conflito(compra: Compra) -> None:
        if next(conflitos, False):
            raise ConflitoRevisaoError(str(compra.id))
        original(compra)

    monkeypatch.setattr(repo, "salvar", salvar_com_um_conflito)
    compra = api.compra_ok(api.replicar())
    assert compra["status"] == "AGUARDANDO_PAGAMENTO"


def test_compra_compensada_no_catalogo_vira_veiculo_indisponivel(
    api: Api, principal: PrincipalFalso
) -> None:
    principal.falhas["bloqueio"] = [(409, "compra-compensada")]
    resposta = api.comprar(api.replicar())
    assert resposta.status_code == 409


def test_recusa_inesperada_em_t3_cancela_por_falha(api: Api, principal: PrincipalFalso) -> None:
    principal.falhas["cobrancas"] = [(409, "compra-compensada")]
    resposta = api.comprar(api.replicar())
    assert resposta.status_code == 503


def test_recusa_inesperada_no_c3_nao_impede_o_cancelamento(
    api: Api, principal: PrincipalFalso, componentes: Componentes, relogio: RelogioFixo
) -> None:
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    principal.falhas["cancelamento"] = [(409, "cobranca-nao-encontrada")]
    relogio.avancar(timedelta(minutes=31))
    componentes.recuperar.executar()
    assert api.compra(compra["id"])["motivo_cancelamento"] == "RESERVA_EXPIRADA"
    assert api.anuncio(veiculo)["status"] == "A_VENDA"


def test_c3_fora_do_ar_na_expiracao_adia_sem_cancelar(
    api: Api, principal: PrincipalFalso, componentes: Componentes, relogio: RelogioFixo
) -> None:
    """Na espera do pagamento, o C3 decide: sem resposta dele, a compra não é cancelada."""
    compra = api.compra_ok(api.replicar())
    principal.fora_do_ar = True
    relogio.avancar(timedelta(minutes=31))
    componentes.recuperar.executar()
    assert api.compra(compra["id"])["status"] == "AGUARDANDO_PAGAMENTO"
    principal.fora_do_ar = False
    relogio.avancar(timedelta(minutes=1))
    componentes.recuperar.executar()
    assert api.compra(compra["id"])["status"] == "CANCELADA"


def test_desbloqueio_recusado_fica_registrado_e_a_compensacao_termina(
    api: Api, principal: PrincipalFalso, componentes: Componentes, relogio: RelogioFixo
) -> None:
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    principal.veiculo(veiculo).status = "VENDIDO"  # anomalia: o catálogo já vendeu
    relogio.avancar(timedelta(minutes=31))
    componentes.recuperar.executar()
    texto = api.http.get("/metrics").text
    assert 'passo="C2_DESBLOQUEIO",resultado="RECUSADO:veiculo-ja-vendido"' in texto
    assert componentes.recuperar.executar().sagas_processadas == 0  # nada mais pendente
    assert api.compra(compra["id"])["status"] == "CANCELADA"


def test_anomalias_depois_do_pivo_sao_registradas_sem_travar(
    api: Api, banco: BancoMongo, principal: PrincipalFalso
) -> None:
    veiculo = api.replicar()
    compra = api.compra_ok(veiculo)
    # O anúncio perdeu a reserva e o catálogo está bloqueado por outra compra.
    banco.db.anuncios.update_one({"_id": veiculo}, {"$set": {"compra_id": str(uuid.uuid4())}})
    principal.veiculo(veiculo).compra_id = str(uuid.uuid4())
    efetivada = api.resultado(compra["id"], principal.pagar(compra["id"]), "PAGO").json()
    assert efetivada["status"] == "EFETIVADA"
    texto = api.http.get("/metrics").text
    assert 'resultado="ANOMALIA_ANUNCIO"' in texto
    assert 'resultado="RECUSADO:bloqueio-de-outra-compra"' in texto


# ---------------------------------------------------------------- caminhos raros da saga


def test_reserva_perdida_entre_a_leitura_e_o_t1(
    api: Api, componentes: Componentes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O anúncio mudou entre a conferência da versão e o T1: a compra é cancelada sem
    chamar o principal."""
    veiculo = api.replicar()
    monkeypatch.setattr(
        componentes.orquestrador._anuncios,
        "reservar",
        lambda *_a: ResultadoReserva.VERSAO_DIVERGENTE,
    )
    resposta = api.comprar(veiculo)
    assert resposta.status_code == 409
    assert tipo_problema(resposta) == "anuncio-alterado"


def test_saga_no_backoff_nao_tenta_antes_da_hora(
    api: Api, principal: PrincipalFalso, componentes: Componentes, banco: BancoMongo
) -> None:
    veiculo = api.replicar()
    principal.fora_do_ar = True
    assert api.comprar(veiculo).status_code == 503  # C2 ficou agendado
    compra_id = banco.db.compras.find_one({"veiculo_id": veiculo})["_id"]  # type: ignore[index]
    compra = componentes.orquestrador._compras.obter(uuid.UUID(compra_id))
    assert compra is not None
    chamadas = len(principal.chamadas)
    componentes.orquestrador.avancar(compra)
    assert len(principal.chamadas) == chamadas


def test_conflito_de_revisao_sem_fim_nao_prende_a_requisicao(
    api: Api, componentes: Componentes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se o documento muda a cada gravação, a saga desiste depois de um número fixo de
    ações e deixa o resto para a recuperação."""
    veiculo = api.replicar()

    def sempre_em_conflito(_compra: object) -> None:
        raise ConflitoRevisaoError

    monkeypatch.setattr(componentes.orquestrador._compras, "salvar", sempre_em_conflito)
    resposta = api.comprar(veiculo)
    assert resposta.status_code == 201
    assert resposta.json()["status"] == "PROCESSANDO"


def test_resultado_disputado_com_outro_repasse(
    api: Api, principal: PrincipalFalso, componentes: Componentes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outro processo grava o resultado entre a leitura e a escrita: relê até 3 vezes e
    segue sem gravar de novo."""
    compra = api.compra_ok(api.replicar())
    caso_uso = componentes.casos_uso.registrar_resultado
    monkeypatch.setattr(caso_uso._compras, "registrar_resultado", lambda *_a: False)
    codigo = principal.cobrancas[compra["id"]].codigo
    resultado = caso_uso.executar(uuid.UUID(compra["id"]), codigo, ResultadoPagamento.PAGO)
    assert resultado.status.value == "AGUARDANDO_PAGAMENTO"


def test_falha_antes_do_pivo_adia_compensacoes_sem_contar_tentativa(
    api: Api, principal: PrincipalFalso, banco: BancoMongo
) -> None:
    """Nenhuma compensação remota foi tentada: tentativas fica zerado e o erro diz o porquê."""
    veiculo = api.replicar()
    principal.falhas["cobrancas"] = [503, 503, 503]  # T3 esgota as tentativas
    assert api.comprar(veiculo).status_code == 503
    documento = banco.db.compras.find_one({"veiculo_id": veiculo})
    assert documento is not None
    saga = documento["saga"]
    assert saga["tentativas"] == 0
    assert saga["ultimo_erro"].startswith("compensações adiadas: T3")
    assert saga["proxima_tentativa"] is not None
    assert "LIBERACAO" in str(saga["compensacoes_feitas"])
