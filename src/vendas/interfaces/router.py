"""Rotas HTTP do serviço de venda: vitrine e compra (públicas) e as rotas internas."""

# Sem `from __future__ import annotations` neste módulo: as dependências usam aliases
# `Annotated[..., Depends(...)]` locais à fábrica, e o FastAPI só os resolve se as
# anotações forem avaliadas na definição da função (com o escopo local).

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response, status

from vendas.application.casos_uso import CasosUsoVendas, FabricaCasosUsoVendas
from vendas.domain.anuncio import DadosAnuncio
from vendas.domain.compra import ResultadoPagamento
from vendas.domain.cpf import CpfInvalidoError
from vendas.domain.erros import (
    AnuncioAlteradoError,
    AnuncioNaoEncontradoError,
    CompraNaoEncontradaError,
    ResultadoDivergenteError,
    ServicoPrincipalIndisponivelError,
    VeiculoIndisponivelError,
)
from vendas.interfaces.esquemas import (
    AnuncioResposta,
    CompraRequisicao,
    CompraResposta,
    PaginaVendidos,
    PaginaVitrine,
    ReplicaAnuncio,
    ReplicaAplicada,
    ResultadoPagamentoRequisicao,
    anuncio_vendido,
    anuncio_vitrine,
)
from vendas.shared.errors import TipoProblema
from vendas.shared.http import ParametrosPaginacao, paginacao, respostas_problema

PRINCIPAL_INDISPONIVEL = TipoProblema(
    "servico-principal-indisponivel", "Serviço principal indisponível", 503
)
PROBLEMAS_VENDAS: dict[type[Exception], TipoProblema] = {
    CpfInvalidoError: TipoProblema("cpf-invalido", "CPF inválido", 422),
    AnuncioNaoEncontradoError: TipoProblema(
        "anuncio-nao-encontrado", "Anúncio não encontrado", 404
    ),
    VeiculoIndisponivelError: TipoProblema("veiculo-indisponivel", "Veículo indisponível", 409),
    AnuncioAlteradoError: TipoProblema("anuncio-alterado", "Anúncio alterado", 409),
    CompraNaoEncontradaError: TipoProblema("compra-nao-encontrada", "Compra não encontrada", 404),
    ResultadoDivergenteError: TipoProblema(
        "resultado-divergente", "Resultado de pagamento divergente", 409
    ),
    ServicoPrincipalIndisponivelError: PRINCIPAL_INDISPONIVEL,
}
# Sugestão ao cliente para tentar de novo (o circuito fica aberto 15 s por padrão).
RETRY_AFTER_SEGUNDOS = "15"

IdVeiculo = Annotated[UUID, Path(description="Identificador do veículo (UUID).")]
IdCompra = Annotated[UUID, Path(description="Identificador da compra (UUID).")]


def criar_router_publico(casos_uso: FabricaCasosUsoVendas) -> APIRouter:
    router = APIRouter()
    CasosUso = Annotated[CasosUsoVendas, Depends(casos_uso)]
    Paginacao = Annotated[ParametrosPaginacao, Depends(paginacao)]

    @router.get(
        "/veiculos/a-venda",
        tags=["Vitrine"],
        summary="Listar veículos à venda (preço ascendente)",
        description=(
            "Público. Anúncios à venda, do mais barato ao mais caro (desempate: data de "
            "cadastro e id). Funciona mesmo com o serviço principal fora do ar."
        ),
        response_model=PaginaVitrine,
        responses=respostas_problema(422),
    )
    def listar_a_venda(uc: CasosUso, pag: Paginacao) -> PaginaVitrine:
        pagina = uc.listar_a_venda.executar(limite=pag.limite, deslocamento=pag.deslocamento)
        return PaginaVitrine(
            itens=[anuncio_vitrine(a) for a in pagina.itens],
            total=pagina.total,
            limite=pagina.limite,
            deslocamento=pagina.deslocamento,
        )

    @router.get(
        "/veiculos/vendidos",
        tags=["Vitrine"],
        summary="Listar veículos vendidos (preço ascendente)",
        description=(
            "Público. Vendidos, ordenados pelo preço da venda (RN-20), sem dados do comprador."
        ),
        response_model=PaginaVendidos,
        responses=respostas_problema(422),
    )
    def listar_vendidos(uc: CasosUso, pag: Paginacao) -> PaginaVendidos:
        pagina = uc.listar_vendidos.executar(limite=pag.limite, deslocamento=pag.deslocamento)
        return PaginaVendidos(
            itens=[anuncio_vendido(a) for a in pagina.itens],
            total=pagina.total,
            limite=pagina.limite,
            deslocamento=pagina.deslocamento,
        )

    @router.get(
        "/anuncios/{veiculo_id}",
        tags=["Vitrine"],
        summary="Consultar anúncio",
        response_model=AnuncioResposta,
        responses=respostas_problema(404, 422),
    )
    def obter_anuncio(uc: CasosUso, veiculo_id: IdVeiculo) -> AnuncioResposta:
        return AnuncioResposta.model_validate(uc.obter_anuncio.executar(veiculo_id))

    @router.post(
        "/compras",
        tags=["Compras"],
        status_code=status.HTTP_201_CREATED,
        summary="Comprar veículo (CPF)",
        description=(
            "Público. Reserva o anúncio, bloqueia a edição no catálogo e gera a cobrança "
            "(saga, passos T1 a T3). Devolve o código do pagamento e o prazo. Em caso de "
            "falha, o que foi feito é desfeito e o veículo volta à vitrine."
        ),
        response_model=CompraResposta,
        responses={
            **respostas_problema(
                400,
                404,
                409,
                422,
                s404="Veículo fora da vitrine",
                s409="Veículo indisponível ou anúncio alterado",
            ),
            503: {"description": "Serviço principal indisponível (compra desfeita)"},
        },
    )
    def comprar(
        uc: CasosUso, corpo: CompraRequisicao, request: Request, response: Response
    ) -> CompraResposta:
        compra = uc.iniciar_compra.executar(corpo.veiculo_id, corpo.versao, corpo.cpf)
        response.headers["Location"] = f"{request.url.path.rstrip('/')}/{compra.id}"
        return CompraResposta.de(compra)

    @router.get(
        "/compras/{compra_id}",
        tags=["Compras"],
        summary="Consultar compra",
        description="Público, pelo identificador (não existe busca por CPF). CPF mascarado.",
        response_model=CompraResposta,
        responses=respostas_problema(404, 422),
    )
    def obter_compra(uc: CasosUso, compra_id: IdCompra) -> CompraResposta:
        return CompraResposta.de(uc.obter_compra.executar(compra_id))

    return router


def criar_router_interno(
    exigir_servico: Callable[..., Any], casos_uso: FabricaCasosUsoVendas
) -> APIRouter:
    """Rotas chamadas pela outbox do principal (docs/05-api.md, seção 5.7)."""
    router = APIRouter(tags=["Interno: principal"], dependencies=[Depends(exigir_servico)])
    CasosUso = Annotated[CasosUsoVendas, Depends(casos_uso)]

    @router.put(
        "/anuncios/{veiculo_id}",
        summary="Réplica do anúncio",
        description="Aplica só versão mais nova; nunca altera a disponibilidade.",
        response_model=ReplicaAplicada,
        responses=respostas_problema(401, 422),
    )
    def replicar(uc: CasosUso, veiculo_id: IdVeiculo, corpo: ReplicaAnuncio) -> ReplicaAplicada:
        dados = DadosAnuncio(veiculo_id=veiculo_id, **corpo.model_dump())
        return ReplicaAplicada(aplicado=uc.atualizar_anuncio.executar(dados))

    @router.post(
        "/compras/{compra_id}/resultado-pagamento",
        summary="Resultado do pagamento (repasse do principal)",
        description=(
            "Registra o resultado e tenta avançar a saga na hora; o que faltar fica para a "
            "recuperação. Repetição: 200 sem efeito. Compra desconhecida: 404 (a outbox "
            "tenta de novo)."
        ),
        response_model=CompraResposta,
        responses=respostas_problema(401, 404, 409, 422),
    )
    def resultado(
        uc: CasosUso, compra_id: IdCompra, corpo: ResultadoPagamentoRequisicao
    ) -> CompraResposta:
        compra = uc.registrar_resultado.executar(
            compra_id, corpo.codigo_pagamento, ResultadoPagamento(corpo.status)
        )
        return CompraResposta.de(compra)

    return router
