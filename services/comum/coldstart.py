"""Instrumentação de cold start compartilhada pelos dois serviços.

Precisa rodar com Uvicorn em 1 worker: cada processo teria a sua própria
"primeira requisição" e geraria falsos cold starts.
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, Request

INSTANCIA = uuid4().hex  # gerado uma vez, ao carregar o módulo
INICIO = time.monotonic()
INICIO_UTC = datetime.now(timezone.utc).isoformat()


class _JsonFormatter(logging.Formatter):
    """Formato entendido pelo Cloud Logging como log estruturado."""

    def format(self, record):
        entrada = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "instancia": INSTANCIA,
        }
        entrada.update(getattr(record, "campos", {}))
        return json.dumps(entrada)


def configura_log(servico: str) -> logging.Logger:
    log = logging.getLogger(servico)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def instrumenta(app: FastAPI, servico: str) -> None:
    """Adiciona os cabeçalhos X-Instance-Id e X-Cold-Start a toda resposta."""
    log = configura_log(servico)
    estado = {"primeira": True}

    @app.middleware("http")
    async def marca_cold_start(request: Request, call_next):
        fria, estado["primeira"] = estado["primeira"], False  # antes do await
        # Idade da instância na chegada da requisição. Separa o cold start de verdade
        # (instância ainda nascendo) da primeira requisição de uma instância já aquecida,
        # como a instância mínima do cenário B.
        idade_ms = int((time.monotonic() - INICIO) * 1000)
        resposta = await call_next(request)
        resposta.headers["X-Instance-Id"] = INSTANCIA
        resposta.headers["X-Cold-Start"] = "1" if fria else "0"
        resposta.headers["X-Instance-Age-Ms"] = str(idade_ms)
        if fria:
            log.info(
                "primeira requisicao da instancia",
                extra={"campos": {
                    "evento": "primeira_requisicao",
                    "servico": servico,
                    "caminho": request.url.path,
                    "segundos_desde_inicio": round(idade_ms / 1000, 3),
                }},
            )
        return resposta

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "instancia": INSTANCIA}

    log.info(
        "instancia iniciada",
        extra={"campos": {
            "evento": "inicio_instancia",
            "servico": servico,
            "inicio_utc": INICIO_UTC,
            "segundos_inicializacao": round(time.monotonic() - INICIO, 3),
        }},
    )


def token_do_cliente(request: Request) -> str | None:
    """Token Firebase do cliente.

    Atrás do API Gateway, o `Authorization` traz o ID token do próprio gateway
    e o token original chega em `X-Forwarded-Authorization`.
    """
    cabecalho = request.headers.get("x-forwarded-authorization") or request.headers.get("authorization")
    if not cabecalho or not cabecalho.lower().startswith("bearer "):
        return None
    return cabecalho[7:].strip()
