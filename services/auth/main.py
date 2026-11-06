"""Auth Service: login e validação de tokens do Firebase Auth (serviço leve)."""

import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from comum.coldstart import instrumenta, token_do_cliente

URL_LOGIN = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"

if os.environ.get("SEM_GCP") == "1":  # testes locais: as funções são trocadas pelos testes
    API_KEY = "teste"

    def verifica_token(token: str) -> dict:
        raise NotImplementedError
else:
    from comum.gcp import inicia_firebase, le_segredo, verifica_token

    API_KEY = le_segredo(os.environ.get("SEGREDO_API_KEY", "firebase-web-api-key"))
    inicia_firebase()

cliente_http = httpx.AsyncClient(timeout=10)

app = FastAPI(title="auth")
instrumenta(app, "auth")


class Credenciais(BaseModel):
    email: str
    senha: str


@app.post("/auth/login")
async def login(credenciais: Credenciais):
    resposta = await cliente_http.post(
        URL_LOGIN,
        params={"key": API_KEY},
        json={"email": credenciais.email, "password": credenciais.senha, "returnSecureToken": True},
    )
    if resposta.status_code != 200:
        raise HTTPException(status_code=401, detail="credenciais invalidas")
    dados = resposta.json()
    return {
        "id_token": dados["idToken"],
        "refresh_token": dados["refreshToken"],
        "expira_em": int(dados["expiresIn"]),
        "uid": dados["localId"],
    }


@app.get("/auth/verify")
def verify(request: Request):
    token = token_do_cliente(request)
    if not token:
        raise HTTPException(status_code=401, detail="token ausente")
    try:
        dados = verifica_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="token invalido")
    return {"uid": dados["uid"], "email": dados.get("email")}
