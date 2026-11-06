"""Inicialização dos clientes da GCP, feita no carregamento do módulo."""

import os

import firebase_admin
from firebase_admin import auth as firebase_auth
from google.cloud import secretmanager

PROJETO = os.environ.get("PROJETO") or os.environ.get("GOOGLE_CLOUD_PROJECT")


def le_segredo(nome: str) -> str:
    cliente = secretmanager.SecretManagerServiceClient()
    caminho = f"projects/{PROJETO}/secrets/{nome}/versions/latest"
    return cliente.access_secret_version(name=caminho).payload.data.decode().strip()


def inicia_firebase() -> None:
    firebase_admin.initialize_app(options={"projectId": PROJETO})


def verifica_token(token: str) -> dict:
    """Valida o ID token do Firebase; levanta exceção se for inválido."""
    return firebase_auth.verify_id_token(token)
