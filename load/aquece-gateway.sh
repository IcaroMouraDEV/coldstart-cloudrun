#!/usr/bin/env bash
# Mantém gateways aquecidos com uma requisição a cada AQUECER_MIN minutos (padrão 5).
# AQUECER no config.env lista caminho:modo, ex.: "aw:404 bw:404 br:real"
#   404  — rota inexistente: o próprio gateway responde 404, sem chegar ao Cloud Run
#   real — GET /posts/seed-0001: passa pelo gateway e chega ao serviço (só para cenários
#          com instância mínima, senão aqueceria também a instância)
# Cada requisição vai para aquecimento.log (instante, caminho, modo, status, segundos), fora do k6.
# Uso: load/aquece-gateway.sh <arquivo_log>   (roda até ser morto)
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
set -a; source "$DIR/config.env"; set +a
LOG="${1:-$DIR/../results/aquecimento.log}"
INTERVALO=$(( ${AQUECER_MIN:-5} * 60 ))
read -r -a ALVOS <<< "${AQUECER:-}"
((${#ALVOS[@]})) || { echo "AQUECER vazio" >&2; exit 1; }

while true; do
  for alvo in "${ALVOS[@]}"; do
    caminho="${alvo%%:*}"; modo="${alvo##*:}"
    var="GATEWAY_${caminho^^}"
    case "$modo" in
      404) rota="/aquecimento-inexistente" ;;
      real) rota="/posts/seed-0001" ;;
      *) echo "modo inválido: $alvo" >&2; exit 1 ;;
    esac
    r=$(curl -s -o /dev/null -m 30 -w '%{http_code} %{time_total}' "${!var}${rota}" || echo "000 0")
    echo "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ) $caminho $modo $r" >> "$LOG"
  done
  sleep "$INTERVALO"
done
