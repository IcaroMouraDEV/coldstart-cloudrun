import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

SERVICES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICES))
os.environ["SEM_GCP"] = "1"


def carrega(servico: str):
    """Importa services/<servico>/main.py como módulo novo (estado de cold start zerado)."""
    nome = f"{servico}_main_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(nome, SERVICES / servico / "main.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def token_falso(token: str) -> dict:
    if token != "valido":
        raise ValueError("token invalido")
    return {"uid": "u1", "email": "u1@teste.com"}


class _Doc:
    def __init__(self, id_, dados):
        self.id, self._dados = id_, dados
        self.exists = dados is not None

    def to_dict(self):
        return dict(self._dados)


class _Ref:
    def __init__(self, banco, id_):
        self._banco, self.id = banco, id_

    def get(self):
        return _Doc(self.id, self._banco.get(self.id))

    def set(self, dados):
        self._banco[self.id] = dict(dados)

    def update(self, dados):
        self._banco[self.id].update(dados)

    def delete(self):
        self._banco.pop(self.id, None)


class ColecaoFalsa:
    """Subconjunto da API de CollectionReference usado pelo backend."""

    def __init__(self):
        self.banco = {}
        self._ordem = None
        self._limite = None

    def document(self, id_=None):
        return _Ref(self.banco, id_ or uuid4().hex)

    def order_by(self, campo, direction="ASCENDING"):
        self._ordem = (campo, direction == "DESCENDING")
        return self

    def limit(self, n):
        self._limite = n
        return self

    def stream(self):
        itens = list(self.banco.items())
        if self._ordem:
            campo, desc = self._ordem
            itens.sort(key=lambda kv: kv[1][campo], reverse=desc)
        return [_Doc(i, d) for i, d in itens[: self._limite]]


@pytest.fixture
def auth():
    modulo = carrega("auth")
    modulo.verifica_token = token_falso
    return modulo, TestClient(modulo.app)


@pytest.fixture
def backend():
    modulo = carrega("backend")
    modulo.verifica_token = token_falso
    modulo.colecao = ColecaoFalsa()
    return modulo, TestClient(modulo.app)
