"""Backend Service: CRUD de postagens no Firestore (inicialização mais pesada)."""

import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from comum.coldstart import instrumenta, token_do_cliente

COLECAO = os.environ.get("COLECAO", "posts")
LIMITE_LISTAGEM = 20

if os.environ.get("SEM_GCP") == "1":  # testes locais: as dependências são trocadas pelos testes
    colecao = None

    def verifica_token(token: str) -> dict:
        raise NotImplementedError
else:
    from google.cloud import firestore

    from comum.gcp import PROJETO, inicia_firebase, verifica_token

    inicia_firebase()
    banco = os.environ.get("FIRESTORE_DB", "(default)")  # um banco por região (infra/env.sh)
    colecao = firestore.Client(project=PROJETO, database=banco).collection(COLECAO)
    # O cliente abre a conexão de forma preguiçosa; uma leitura aqui faz a conexão
    # com o banco entrar no cold start, como numa aplicação real.
    list(colecao.limit(1).stream())

app = FastAPI(title="backend")
instrumenta(app, "backend")


class Post(BaseModel):
    titulo: str = Field(max_length=200)
    conteudo: str = Field(max_length=5000)


def exige_usuario(request: Request) -> str:
    token = token_do_cliente(request)
    if not token:
        raise HTTPException(status_code=401, detail="token ausente")
    try:
        return verifica_token(token)["uid"]
    except Exception:
        raise HTTPException(status_code=401, detail="token invalido")


def _agora():
    return datetime.now(timezone.utc)


def _serializa(doc) -> dict:
    dados = doc.to_dict()
    for campo in ("criado_em", "atualizado_em"):
        if isinstance(dados.get(campo), datetime):
            dados[campo] = dados[campo].isoformat()
    return {"id": doc.id, **dados}


@app.get("/posts")
def lista_posts():
    docs = colecao.order_by("criado_em", direction="DESCENDING").limit(LIMITE_LISTAGEM).stream()
    return [_serializa(d) for d in docs]


@app.get("/posts/{post_id}")
def busca_post(post_id: str):
    doc = colecao.document(post_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="post nao encontrado")
    return _serializa(doc)


@app.post("/posts", status_code=201)
def cria_post(post: Post, request: Request):
    uid = exige_usuario(request)
    ref = colecao.document()
    agora = _agora()
    ref.set({**post.model_dump(), "autor": uid, "criado_em": agora, "atualizado_em": agora})
    return {"id": ref.id}


@app.put("/posts/{post_id}")
def atualiza_post(post_id: str, post: Post, request: Request):
    exige_usuario(request)
    ref = colecao.document(post_id)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail="post nao encontrado")
    ref.update({**post.model_dump(), "atualizado_em": _agora()})
    return {"id": post_id}


@app.delete("/posts/{post_id}", status_code=204)
def remove_post(post_id: str, request: Request):
    exige_usuario(request)
    colecao.document(post_id).delete()
    return Response(status_code=204)
