"""CPF (RN-05, RN-06) e o agregado Compra com o estado da saga."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from apoio.cenario import AS_10H, CPF, CPF_DIGITOS, CPF_MASCARADO
from vendas.domain.compra import (
    Compensacao,
    Compra,
    MotivoCancelamento,
    Passo,
    ResultadoPagamento,
    StatusCompra,
)
from vendas.domain.cpf import Cpf, CpfInvalidoError
from vendas.domain.erros import ResultadoDivergenteError


@pytest.mark.parametrize("texto", [CPF, CPF_DIGITOS, f"  {CPF} ", "529.982.24725"])
def test_cpf_valido_com_ou_sem_pontuacao(texto: str) -> None:
    cpf = Cpf.de(texto)
    assert cpf.digitos == CPF_DIGITOS
    assert cpf.mascarado == CPF_MASCARADO
    assert str(cpf) == CPF_MASCARADO
    assert CPF_DIGITOS not in repr(cpf)


@pytest.mark.parametrize(
    "texto",
    ["529.982.247-24", "111.111.111-11", "000.000.000-00", "5299822472", "abc", "529-982-247.25"],
)
def test_cpf_invalido(texto: str) -> None:
    with pytest.raises(CpfInvalidoError) as erro:
        Cpf.de(texto)
    assert texto.strip() not in str(erro.value)
    assert erro.value.erros == [("cpf", "CPF inválido (formato ou dígitos verificadores)")]


def test_cpf_com_digito_verificador_zero() -> None:
    assert Cpf.de("000.000.001-91").mascarado == "***.000.001-**"


def nova() -> Compra:
    return Compra.iniciar(
        veiculo_id=uuid4(),
        versao_anuncio=1,
        marca="Fiat",
        modelo="Argo",
        ano=2022,
        cor="Prata",
        preco=Decimal("72000.00"),
        cpf=Cpf.de(CPF),
        cpf_cifrado="k1:xyz",
        agora=AS_10H,
        prazo=timedelta(minutes=30),
    )


def test_compra_nasce_processando_com_prazo_e_evento() -> None:
    compra = nova()
    assert compra.status is StatusCompra.PROCESSANDO
    assert compra.ativa
    assert compra.expira_em == AS_10H + timedelta(minutes=30)
    assert compra.cpf_mascarado == CPF_MASCARADO
    evento = compra.coletar_eventos()[0]
    assert evento.nome == "CompraIniciada"
    assert CPF_DIGITOS not in str(evento.como_dict())


def test_passos_e_espera_do_pagamento() -> None:
    compra = nova()
    for passo in (Passo.RESERVA, Passo.BLOQUEIO):
        compra.concluir_passo(passo, AS_10H)
    compra.registrar_cobranca("PAG-000000000001", AS_10H)
    assert compra.status is StatusCompra.AGUARDANDO_PAGAMENTO
    assert not compra.expirada(AS_10H)
    assert compra.expirada(AS_10H + timedelta(minutes=30))
    nomes = [e.nome for e in compra.coletar_eventos()]
    assert nomes.count("PassoSagaExecutado") == 3
    assert nomes[-1] == "CompraAguardandoPagamento"


def test_resultado_idempotente_e_divergente() -> None:
    compra = nova()
    compra.registrar_cobranca("PAG-000000000001", AS_10H)
    assert compra.registrar_resultado(ResultadoPagamento.PAGO, "PAG-000000000001", AS_10H)
    assert not compra.registrar_resultado(ResultadoPagamento.PAGO, "PAG-000000000001", AS_10H)
    with pytest.raises(ResultadoDivergenteError):
        compra.registrar_resultado(ResultadoPagamento.CANCELADO, "PAG-000000000001", AS_10H)
    with pytest.raises(ResultadoDivergenteError):
        compra.registrar_resultado(ResultadoPagamento.PAGO, "PAG-999999999999", AS_10H)


def test_cancelamento_agenda_compensacoes_em_ordem_inversa() -> None:
    compra = nova()
    for passo in (Passo.RESERVA, Passo.BLOQUEIO, Passo.COBRANCA):
        compra.iniciar_passo(passo, AS_10H)
    compra.cancelar(
        MotivoCancelamento.RESERVA_EXPIRADA,
        AS_10H,
        ja_feitas=frozenset({Compensacao.CANCELAMENTO_COBRANCA}),
    )
    assert not compra.ativa
    assert compra.saga.compensacoes_pendentes == [Compensacao.DESBLOQUEIO, Compensacao.LIBERACAO]
    assert compra.saga.compensacoes_feitas == [Compensacao.CANCELAMENTO_COBRANCA]
    compra.concluir_compensacao(Compensacao.DESBLOQUEIO, AS_10H)
    assert compra.saga.compensacoes_pendentes == [Compensacao.LIBERACAO]


def test_retentativa_com_backoff_e_teto() -> None:
    compra = nova()
    compra.agendar_retentativa("x" * 400, AS_10H)
    assert compra.saga.proxima_tentativa == AS_10H + timedelta(seconds=2)
    assert len(compra.saga.ultimo_erro or "") == 300
    assert not compra.pode_tentar(AS_10H)
    for _ in range(10):
        compra.agendar_retentativa("erro", AS_10H)
    assert compra.saga.proxima_tentativa == AS_10H + timedelta(minutes=5)
    compra.concluir_passo(Passo.RESERVA, AS_10H)
    assert compra.pode_tentar(AS_10H)


def test_efetivacao_e_apagar_cpf() -> None:
    compra = nova()
    compra.efetivar(AS_10H)
    assert (compra.status, compra.data_venda) == (StatusCompra.EFETIVADA, AS_10H)
    compra.apagar_cpf(AS_10H)
    assert compra.cpf_cifrado is None
    assert compra.cpf_mascarado is None
