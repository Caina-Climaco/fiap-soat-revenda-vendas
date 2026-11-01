"""Cifra do CPF com AES-256-GCM (ADR-005 do serviço de venda).

Formato gravado: `<id da chave>:<base64(nonce de 12 bytes + texto cifrado + tag)>`. O id
da chave permite rotação: cifra com a chave atual e decifra com qualquer chave conhecida.
O id da compra entra como dado associado, então a cifra de uma compra não decifra em outra.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class ChaveDesconhecidaError(ValueError):
    pass


class CifradorAesGcm:
    def __init__(self, chaves: Mapping[str, bytes], chave_atual: str) -> None:
        if chave_atual not in chaves:
            raise ChaveDesconhecidaError(chave_atual)
        self._chaves = {kid: AESGCM(chave) for kid, chave in chaves.items()}
        self._atual = chave_atual

    def cifrar(self, texto: str, associado: str) -> str:
        nonce = os.urandom(12)
        cifrado = self._chaves[self._atual].encrypt(nonce, texto.encode(), associado.encode())
        return f"{self._atual}:{base64.b64encode(nonce + cifrado).decode()}"

    def decifrar(self, cifrado: str, associado: str) -> str:
        kid, _, conteudo = cifrado.partition(":")
        if kid not in self._chaves:
            raise ChaveDesconhecidaError(kid)
        bruto = base64.b64decode(conteudo)
        return self._chaves[kid].decrypt(bruto[:12], bruto[12:], associado.encode()).decode()
