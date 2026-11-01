"""Exceções do contexto Vendas. Mensagens em português, sem dados pessoais."""

from __future__ import annotations

from uuid import UUID


class VendasError(Exception):
    """Base das exceções de negócio de Vendas."""


class AnuncioNaoEncontradoError(VendasError):
    def __init__(self, veiculo_id: UUID) -> None:
        self.veiculo_id = veiculo_id
        super().__init__(f"O veículo {veiculo_id} não está na vitrine.")


class VeiculoIndisponivelError(VendasError):
    """Anúncio reservado ou vendido, ou outra compra venceu a disputa (RN-03)."""

    def __init__(self, veiculo_id: UUID) -> None:
        self.veiculo_id = veiculo_id
        super().__init__(f"O veículo {veiculo_id} não está disponível para compra.")


class AnuncioAlteradoError(VendasError):
    """A versão enviada pelo comprador não é a atual (RN-12)."""

    def __init__(self, veiculo_id: UUID) -> None:
        self.veiculo_id = veiculo_id
        super().__init__(
            f"O anúncio do veículo {veiculo_id} foi alterado; consulte-o novamente e "
            "refaça a compra com os dados atuais."
        )


class CompraNaoEncontradaError(VendasError):
    def __init__(self, compra_id: UUID) -> None:
        self.compra_id = compra_id
        super().__init__(f"A compra {compra_id} não existe.")


class ServicoPrincipalIndisponivelError(VendasError):
    """Falha de T2 ou T3 por indisponibilidade do principal; a compra foi compensada."""

    def __init__(self, compra_id: UUID) -> None:
        self.compra_id = compra_id
        super().__init__(
            "O serviço principal não respondeu e a compra foi desfeita; o veículo voltou à "
            "vitrine. Tente de novo em instantes."
        )


class ResultadoDivergenteError(VendasError):
    """Resultado de pagamento diferente de um já registrado para a mesma compra."""

    def __init__(self, compra_id: UUID) -> None:
        self.compra_id = compra_id
        super().__init__(f"A compra {compra_id} já tem outro resultado de pagamento registrado.")
