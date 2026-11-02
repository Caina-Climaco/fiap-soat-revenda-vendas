"""Repositórios MongoDB de Anuncio e Compra (ADR-001 do serviço de venda).

- Dinheiro em centavos (inteiro): ordenação e comparação exatas, sem ponto flutuante.
- Identificadores como texto (UUID canônico), legíveis em consultas manuais.
- Toda mudança de estado é atômica num documento: `find_one_and_update` e `update_one`
  condicionais ou `replace_one` com a `revisao` lida (controle otimista).
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, OperationFailure

from vendas.domain.anuncio import Anuncio, DadosAnuncio, StatusAnuncio
from vendas.domain.compra import (
    Compensacao,
    Compra,
    EstadoSaga,
    MotivoCancelamento,
    Passo,
    ResultadoPagamento,
    StatusCompra,
)
from vendas.domain.repositorio import (
    CompraAtivaExistenteError,
    ConflitoRevisaoError,
    ResultadoReserva,
)
from vendas.shared.mongo import utc

Documento = dict[str, Any]


def centavos(valor: Decimal) -> int:
    return int((valor * 100).to_integral_value())


def reais(valor: int) -> Decimal:
    return (Decimal(valor) / 100).quantize(Decimal("0.01"))


def _data(valor: Any) -> datetime:
    data = utc(valor)
    assert data is not None  # noqa: S101 - campo obrigatório no documento
    return data


# Índices substituídos: mudar as chaves mantendo o nome faria o create_index falhar num
# banco já implantado; os novos têm outro nome e os antigos são removidos.
_INDICES_SUBSTITUIDOS = {"anuncios": ("ix_vitrine_a_venda", "ix_vitrine_vendidos")}


def garantir_indices(db: Database[Documento]) -> None:
    """Idempotente: roda na subida da aplicação e do CronJob."""
    for colecao, nomes in _INDICES_SUBSTITUIDOS.items():
        existentes = db[colecao].index_information()
        for nome in nomes:
            # Outra réplica (ou o CronJob) pode remover entre a leitura e o drop: na subida
            # da versão nova, várias rodam esta função ao mesmo tempo.
            if nome in existentes:
                with suppress(OperationFailure):
                    db[colecao].drop_index(nome)
    # As listagens ordenam por preço, data e `_id` (desempate estável na paginação): com o
    # `_id` como última chave, o índice atende o sort inteiro, sem ordenação em memória.
    db.anuncios.create_index(
        [
            ("status", ASCENDING),
            ("preco_centavos", ASCENDING),
            ("criado_em", ASCENDING),
            ("_id", ASCENDING),
        ],
        name="ix_vitrine_a_venda_ordem",
    )
    db.anuncios.create_index(
        [
            ("status", ASCENDING),
            ("preco_venda_centavos", ASCENDING),
            ("data_venda", ASCENDING),
            ("_id", ASCENDING),
        ],
        name="ix_vitrine_vendidos_ordem",
    )
    # RN-03: no máximo uma compra ativa por veículo, garantido pelo banco.
    db.compras.create_index(
        "veiculo_id",
        name="ux_compra_ativa_por_veiculo",
        unique=True,
        partialFilterExpression={"ativa": True},
    )
    db.compras.create_index(
        [("status", ASCENDING), ("saga.proxima_tentativa", ASCENDING)], name="ix_recuperacao"
    )
    # Retenção do CPF (canceladas_com_cpf_ate): só as canceladas entram no índice.
    db.compras.create_index(
        [("status", ASCENDING), ("atualizada_em", ASCENDING)],
        name="ix_retencao_cpf",
        partialFilterExpression={"status": StatusCompra.CANCELADA.value},
    )


# ---------------------------------------------------------------- anúncios


class MongoAnuncioRepository:
    def __init__(self, db: Database[Documento]) -> None:
        self._colecao: Collection[Documento] = db.anuncios

    def aplicar_replica(self, dados: DadosAnuncio, agora: datetime) -> bool:
        descricao = {
            "versao": dados.versao,
            "marca": dados.marca,
            "modelo": dados.modelo,
            "ano": dados.ano,
            "cor": dados.cor,
            "preco_centavos": centavos(dados.preco),
            "atualizado_em": agora,
        }
        filtro = {"_id": str(dados.veiculo_id), "versao": {"$lt": dados.versao}}
        if self._colecao.update_one(filtro, {"$set": descricao}).matched_count:
            return True
        try:
            self._colecao.insert_one(_anuncio_para_doc(Anuncio.novo(dados, agora)))
        except DuplicateKeyError:
            # Outra entrega inseriu o anúncio entre o update e o insert (ex.: v1 e v2 em
            # corrida). Se a dela for mais antiga, esta ainda precisa ser aplicada.
            return bool(self._colecao.update_one(filtro, {"$set": descricao}).matched_count)
        return True

    def obter(self, veiculo_id: UUID) -> Anuncio | None:
        documento = self._colecao.find_one({"_id": str(veiculo_id)})
        return None if documento is None else _anuncio_de_doc(documento)

    def reservar(
        self, veiculo_id: UUID, versao: int, compra_id: UUID, agora: datetime
    ) -> ResultadoReserva:
        reservado = self._colecao.find_one_and_update(
            {
                "_id": str(veiculo_id),
                "$or": [
                    {"status": StatusAnuncio.A_VENDA.value, "versao": versao},
                    {"status": StatusAnuncio.RESERVADO.value, "compra_id": str(compra_id)},
                ],
            },
            {
                "$set": {
                    "status": StatusAnuncio.RESERVADO.value,
                    "compra_id": str(compra_id),
                    "atualizado_em": agora,
                }
            },
        )
        if reservado is not None:
            return ResultadoReserva.RESERVADO
        atual = self._colecao.find_one({"_id": str(veiculo_id)})
        if atual is None:
            return ResultadoReserva.NAO_ENCONTRADO
        if atual["versao"] != versao:
            return ResultadoReserva.VERSAO_DIVERGENTE
        return ResultadoReserva.INDISPONIVEL

    def liberar(self, veiculo_id: UUID, compra_id: UUID, agora: datetime) -> bool:
        liberado = self._colecao.update_one(
            {
                "_id": str(veiculo_id),
                "status": StatusAnuncio.RESERVADO.value,
                "compra_id": str(compra_id),
            },
            {
                "$set": {
                    "status": StatusAnuncio.A_VENDA.value,
                    "compra_id": None,
                    "atualizado_em": agora,
                }
            },
        )
        return liberado.modified_count == 1

    def vender(
        self, veiculo_id: UUID, compra_id: UUID, preco: Decimal, data_venda: datetime
    ) -> bool:
        filtro = {"_id": str(veiculo_id), "compra_id": str(compra_id)}
        vendido = self._colecao.update_one(
            {**filtro, "status": StatusAnuncio.RESERVADO.value},
            {
                "$set": {
                    "status": StatusAnuncio.VENDIDO.value,
                    "preco_venda_centavos": centavos(preco),
                    "data_venda": data_venda,
                    "atualizado_em": data_venda,
                }
            },
        )
        if vendido.matched_count:
            return True
        # Repetição de T5 (queda depois de vender e antes de gravar a compra).
        return self._colecao.count_documents({**filtro, "status": StatusAnuncio.VENDIDO.value}) > 0

    def listar(
        self, status: StatusAnuncio, *, limite: int, deslocamento: int
    ) -> tuple[Sequence[Anuncio], int]:
        filtro = {"status": status.value}
        ordem = (
            [("preco_venda_centavos", ASCENDING), ("data_venda", ASCENDING), ("_id", ASCENDING)]
            if status is StatusAnuncio.VENDIDO
            else [("preco_centavos", ASCENDING), ("criado_em", ASCENDING), ("_id", ASCENDING)]
        )
        total = self._colecao.count_documents(filtro)
        documentos = self._colecao.find(filtro).sort(ordem).skip(deslocamento).limit(limite)
        return [_anuncio_de_doc(d) for d in documentos], total


def _anuncio_para_doc(anuncio: Anuncio) -> Documento:
    return {
        "_id": str(anuncio.veiculo_id),
        "versao": anuncio.versao,
        "marca": anuncio.marca,
        "modelo": anuncio.modelo,
        "ano": anuncio.ano,
        "cor": anuncio.cor,
        "preco_centavos": centavos(anuncio.preco),
        "status": anuncio.status.value,
        "compra_id": None if anuncio.compra_id is None else str(anuncio.compra_id),
        "preco_venda_centavos": None
        if anuncio.preco_venda is None
        else centavos(anuncio.preco_venda),
        "data_venda": anuncio.data_venda,
        "criado_em": anuncio.criado_em,
        "atualizado_em": anuncio.atualizado_em,
    }


def _anuncio_de_doc(documento: Documento) -> Anuncio:
    compra_id = documento.get("compra_id")
    preco_venda = documento.get("preco_venda_centavos")
    return Anuncio(
        veiculo_id=UUID(documento["_id"]),
        versao=documento["versao"],
        marca=documento["marca"],
        modelo=documento["modelo"],
        ano=documento["ano"],
        cor=documento["cor"],
        preco=reais(documento["preco_centavos"]),
        status=StatusAnuncio(documento["status"]),
        compra_id=None if compra_id is None else UUID(compra_id),
        preco_venda=None if preco_venda is None else reais(preco_venda),
        data_venda=utc(documento.get("data_venda")),
        criado_em=_data(documento["criado_em"]),
        atualizado_em=_data(documento["atualizado_em"]),
    )


# ---------------------------------------------------------------- compras


class MongoCompraRepository:
    def __init__(self, db: Database[Documento]) -> None:
        self._colecao: Collection[Documento] = db.compras

    def inserir(self, compra: Compra) -> None:
        try:
            self._colecao.insert_one(_compra_para_doc(compra))
        except DuplicateKeyError as exc:
            raise CompraAtivaExistenteError(str(compra.veiculo_id)) from exc

    def obter(self, compra_id: UUID) -> Compra | None:
        documento = self._colecao.find_one({"_id": str(compra_id)})
        return None if documento is None else _compra_de_doc(documento)

    def salvar(self, compra: Compra) -> None:
        documento = _compra_para_doc(compra)
        documento["revisao"] = compra.revisao + 1
        try:
            gravado = self._colecao.replace_one(
                {"_id": str(compra.id), "revisao": compra.revisao}, documento
            )
        except DuplicateKeyError as exc:  # pragma: no cover - a reserva já impede
            raise CompraAtivaExistenteError(str(compra.veiculo_id)) from exc
        if gravado.matched_count == 0:
            raise ConflitoRevisaoError(str(compra.id))
        compra.revisao += 1

    def registrar_resultado(
        self, compra_id: UUID, resultado: ResultadoPagamento, codigo: str, agora: datetime
    ) -> bool:
        gravado = self._colecao.update_one(
            {"_id": str(compra_id), "resultado_pagamento": None},
            {
                "$set": {
                    "resultado_pagamento": resultado.value,
                    "codigo_pagamento": codigo,
                    "atualizada_em": agora,
                },
                "$inc": {"revisao": 1},
            },
        )
        return gravado.modified_count == 1

    def tomar_lease(
        self, compra_id: UUID, dono: str, agora: datetime, duracao: timedelta
    ) -> Compra | None:
        documento = self._colecao.find_one_and_update(
            {
                "_id": str(compra_id),
                # Sem reentrância: o dono é único por execução (OrquestradorCompra.novo_dono),
                # então só um lease livre ou vencido pode ser tomado.
                "$or": [{"saga.lease_ate": None}, {"saga.lease_ate": {"$lt": agora}}],
            },
            {
                "$set": {"saga.lease_ate": agora + duracao, "saga.dono_lease": dono},
                "$inc": {"revisao": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        return None if documento is None else _compra_de_doc(documento)

    def liberar_lease(self, compra_id: UUID, dono: str) -> None:
        self._colecao.update_one(
            {"_id": str(compra_id), "saga.dono_lease": dono},
            {
                "$set": {"saga.lease_ate": None, "saga.dono_lease": None},
                "$inc": {"revisao": 1},
            },
        )

    def pendentes(self, agora: datetime, limite: int) -> list[UUID]:
        livre = {"$or": [{"saga.lease_ate": None}, {"saga.lease_ate": {"$lt": agora}}]}
        no_prazo = {
            "$or": [{"saga.proxima_tentativa": None}, {"saga.proxima_tentativa": {"$lte": agora}}]
        }
        documentos = (
            self._colecao.find({"$and": [_algo_a_fazer(agora), livre, no_prazo]}, {"_id": 1})
            .sort("criada_em", ASCENDING)
            .limit(limite)
        )
        return [UUID(d["_id"]) for d in documentos]

    def contar_paradas(self, sem_progresso_desde: datetime) -> int:
        # `_algo_a_fazer(sem_progresso_desde)`, não do instante atual: a compra que acabou de
        # expirar tem `atualizada_em` antigo (de quando entrou na espera do pagamento), mas só
        # agora passou a ter algo a fazer; ela não está parada, a recuperação ainda nem passou.
        return self._colecao.count_documents(
            {
                "$and": [
                    _algo_a_fazer(sem_progresso_desde),
                    {"atualizada_em": {"$lt": sem_progresso_desde}},
                ]
            }
        )

    def canceladas_com_cpf_ate(self, limite_data: datetime, limite: int) -> list[UUID]:
        documentos = self._colecao.find(
            {
                "status": StatusCompra.CANCELADA.value,
                "atualizada_em": {"$lt": limite_data},
                "cpf.cifrado": {"$ne": None},
            },
            {"_id": 1},
        ).limit(limite)
        return [UUID(d["_id"]) for d in documentos]


def _algo_a_fazer(agora: datetime) -> Documento:
    """Compras cuja saga ainda tem passo, compensação ou decisão a tomar (ADR-006)."""
    return {
        "$or": [
            {"status": StatusCompra.PROCESSANDO.value},
            {
                "status": StatusCompra.AGUARDANDO_PAGAMENTO.value,
                "$or": [
                    {"expira_em": {"$lte": agora}},
                    {"resultado_pagamento": {"$ne": None}},
                ],
            },
            {
                "status": StatusCompra.EFETIVADA.value,
                "saga.concluidos": {"$ne": Passo.VENDA_CATALOGO.value},
            },
            {
                "status": StatusCompra.CANCELADA.value,
                "saga.compensacoes_pendentes.0": {"$exists": True},
            },
        ]
    }


def _compra_para_doc(compra: Compra) -> Documento:
    saga = compra.saga
    return {
        "_id": str(compra.id),
        "veiculo_id": str(compra.veiculo_id),
        "ativa": compra.ativa,
        "versao_anuncio": compra.versao_anuncio,
        "veiculo": {
            "marca": compra.marca,
            "modelo": compra.modelo,
            "ano": compra.ano,
            "cor": compra.cor,
        },
        "preco_centavos": centavos(compra.preco),
        "cpf": {"cifrado": compra.cpf_cifrado, "mascarado": compra.cpf_mascarado},
        "status": compra.status.value,
        "motivo_cancelamento": None
        if compra.motivo_cancelamento is None
        else compra.motivo_cancelamento.value,
        "codigo_pagamento": compra.codigo_pagamento,
        "resultado_pagamento": None
        if compra.resultado_pagamento is None
        else compra.resultado_pagamento.value,
        "criada_em": compra.criada_em,
        "expira_em": compra.expira_em,
        "data_venda": compra.data_venda,
        "atualizada_em": compra.atualizada_em,
        "saga": {
            "iniciados": [p.value for p in saga.iniciados],
            "concluidos": [p.value for p in saga.concluidos],
            "compensacoes_pendentes": [c.value for c in saga.compensacoes_pendentes],
            "compensacoes_feitas": [c.value for c in saga.compensacoes_feitas],
            "tentativas": saga.tentativas,
            "proxima_tentativa": saga.proxima_tentativa,
            "ultimo_erro": saga.ultimo_erro,
            "lease_ate": saga.lease_ate,
            "dono_lease": saga.dono_lease,
        },
        "revisao": compra.revisao,
    }


def _compra_de_doc(documento: Documento) -> Compra:
    saga = documento["saga"]
    veiculo = documento["veiculo"]
    motivo = documento.get("motivo_cancelamento")
    resultado = documento.get("resultado_pagamento")
    return Compra(
        id=UUID(documento["_id"]),
        veiculo_id=UUID(documento["veiculo_id"]),
        versao_anuncio=documento["versao_anuncio"],
        marca=veiculo["marca"],
        modelo=veiculo["modelo"],
        ano=veiculo["ano"],
        cor=veiculo["cor"],
        preco=reais(documento["preco_centavos"]),
        cpf_cifrado=documento["cpf"]["cifrado"],
        cpf_mascarado=documento["cpf"]["mascarado"],
        status=StatusCompra(documento["status"]),
        motivo_cancelamento=None if motivo is None else MotivoCancelamento(motivo),
        codigo_pagamento=documento.get("codigo_pagamento"),
        resultado_pagamento=None if resultado is None else ResultadoPagamento(resultado),
        criada_em=_data(documento["criada_em"]),
        expira_em=_data(documento["expira_em"]),
        data_venda=utc(documento.get("data_venda")),
        atualizada_em=_data(documento["atualizada_em"]),
        saga=EstadoSaga(
            iniciados=[Passo(p) for p in saga["iniciados"]],
            concluidos=[Passo(p) for p in saga["concluidos"]],
            compensacoes_pendentes=[Compensacao(c) for c in saga["compensacoes_pendentes"]],
            compensacoes_feitas=[Compensacao(c) for c in saga["compensacoes_feitas"]],
            tentativas=saga["tentativas"],
            proxima_tentativa=utc(saga.get("proxima_tentativa")),
            ultimo_erro=saga.get("ultimo_erro"),
            lease_ate=utc(saga.get("lease_ate")),
            dono_lease=saga.get("dono_lease"),
        ),
        revisao=documento["revisao"],
    )
