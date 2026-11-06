"""Carga inicial: usuários de teste no Firebase Auth e postagens nas coleções dos cenários.

Uso (com `gcloud auth application-default login` feito):
    SENHA_TESTE=... uv run --with firebase-admin python infra/50-seed.py --usuarios 300
    SENHA_TESTE=... uv run --with firebase-admin python infra/50-seed.py --usuarios 300 \
        --prefixo k6-e1 --banco coldstart-e1        # região us-east1 (ver infra/env.sh)

Cada região tem as suas contas (a cota de verificação de senha do Firebase é por conta)
e o seu banco Firestore.

As postagens têm IDs fixos (seed-0000 ... seed-0999) para o k6 sortear leituras
e atualizações sem precisar consultar o banco. Rodar de novo restaura o estado.
"""

import argparse
import os
import subprocess
from datetime import datetime, timedelta, timezone

import firebase_admin
from firebase_admin import auth, firestore

LOTE = 500  # limite de escritas por batch do Firestore


def projeto_ativo() -> str:
    return os.environ.get("PROJETO") or subprocess.check_output(
        ["gcloud", "config", "get-value", "project"], text=True
    ).strip()


def cria_usuarios(n: int, senha: str, prefixo: str) -> None:
    for i in range(n):
        email = f"{prefixo}-{i:03d}@coldstart.test"
        try:
            usuario = auth.get_user_by_email(email)
            auth.update_user(usuario.uid, password=senha)
        except auth.UserNotFoundError:
            auth.create_user(email=email, password=senha)
    print(f"{n} usuários prontos ({prefixo}-000 ... {prefixo}-{n - 1:03d}@coldstart.test)")


def popula_colecao(db, nome: str, n: int) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for inicio in range(0, n, LOTE):
        lote = db.batch()
        for i in range(inicio, min(inicio + LOTE, n)):
            criado = base + timedelta(minutes=i)
            lote.set(db.collection(nome).document(f"seed-{i:04d}"), {
                "titulo": f"Postagem {i}",
                "conteudo": "Lorem ipsum dolor sit amet. " * 20,
                "autor": "seed",
                "criado_em": criado,
                "atualizado_em": criado,
            })
        lote.commit()
    print(f"{nome}: {n} postagens")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--usuarios", type=int, default=20)
    parser.add_argument("--posts", type=int, default=1000)
    parser.add_argument("--cenarios", default="abc")
    parser.add_argument("--prefixo", default="k6", help="e-mails <prefixo>-NNN@coldstart.test")
    parser.add_argument("--banco", default="(default)", help="banco Firestore da região")
    args = parser.parse_args()

    senha = os.environ["SENHA_TESTE"]
    projeto = projeto_ativo()
    firebase_admin.initialize_app(options={"projectId": projeto})
    db = firestore.client(database_id=None if args.banco == "(default)" else args.banco)

    cria_usuarios(args.usuarios, senha, args.prefixo)
    for c in args.cenarios:
        popula_colecao(db, f"posts_{c}", args.posts)


if __name__ == "__main__":
    main()
