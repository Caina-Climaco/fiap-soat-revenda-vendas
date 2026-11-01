"""Value object CPF (RN-05, RN-06 e ADR-005 do serviço de venda).

Valida formato e dígitos verificadores e oferece só a forma mascarada para exibição.
O valor completo existe apenas em memória, para ser cifrado antes de ir ao banco, e não
aparece em `repr`, logs ou mensagens de erro.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FORMATO = re.compile(r"^\d{3}\.?\d{3}\.?\d{3}-?\d{2}$")


class CpfInvalidoError(ValueError):
    def __init__(self) -> None:
        self.erros = [("cpf", "CPF inválido (formato ou dígitos verificadores)")]
        super().__init__("O CPF informado é inválido.")


def _digito(base: str) -> int:
    peso_inicial = len(base) + 1
    soma = sum(int(d) * peso for d, peso in zip(base, range(peso_inicial, 1, -1), strict=True))
    resto = soma * 10 % 11
    return 0 if resto == 10 else resto


@dataclass(frozen=True, slots=True)
class Cpf:
    digitos: str = field(repr=False)

    @classmethod
    def de(cls, texto: str) -> Cpf:
        """Aceita `52998224725` ou `529.982.247-25`; recusa o resto."""
        limpo = texto.strip()
        if not _FORMATO.match(limpo):
            raise CpfInvalidoError
        digitos = re.sub(r"\D", "", limpo)
        if len(set(digitos)) == 1:  # 000.000.000-00, 111.111.111-11...
            raise CpfInvalidoError
        if _digito(digitos[:9]) != int(digitos[9]) or _digito(digitos[:10]) != int(digitos[10]):
            raise CpfInvalidoError
        return cls(digitos)

    @property
    def mascarado(self) -> str:
        """`***.982.247-**`: identifica a compra para o próprio comprador sem expor o CPF."""
        d = self.digitos
        return f"***.{d[3:6]}.{d[6:9]}-**"

    def __str__(self) -> str:
        return self.mascarado
