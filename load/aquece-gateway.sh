#!/usr/bin/env bash
# Mantém gateways aquecidos com uma requisição periódica.
# AQUECER no config.env lista caminho:modo[:minutos], ex.: "aw:404 bw:404 br:real" ou "bw10:404:10 bw15:404:15"
#   404  — rota inexistente: o próprio gateway responde 404, sem chegar ao Cloud Run
#   real — GET /posts/seed-0001: passa pelo gateway e chega ao serviço (só para cenários
#          com instância mínima, senão aqueceria também a instância)
#   minutos — intervalo daquele alvo; sem ele vale AQUECER_MIN (padrão 5)
# Cada requisição vai para o log (instante, caminho, modo, status, segundos), fora do k6.
# Uso: load/aquece-gateway.sh <arquivo_log>   (roda até ser morto)
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
set -a; source "$DIR/config.env"; set +a
LOG="${1:-$DIR/../results/aquecimento.log}"
read -r -a ALVOS <<< "${AQUECER:-}"
((${#ALVOS[@]})) || { echo "AQUECER vazio" >&2; exit 1; }

declare -A PROXIMO
while true; do
  agora=$(date +%s)
  for alvo in "${ALVOS[@]}"; do
    IFS=: read -r caminho modo minutos <<< "$alvo"
    ((agora >= ${PROXIMO[$caminho]:-0})) || continue
    PROXIMO[$caminho]=$((agora + ${minutos:-${AQUECER_MIN:-5}} * 60))
    var="GATEWAY_${caminho^^}"
    case "$modo" in
      404) rota="/aquecimento-inexistente" ;;
      real) rota="/posts/seed-0001" ;;
      *) echo "modo inválido: $alvo" >&2; exit 1 ;;
    esac
    r=$(curl -s -o /dev/null -m 30 -w '%{http_code} %{time_total}' "${!var}${rota}" || echo "000 0")
    echo "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ) $caminho $modo $r" >> "$LOG"
  done
  sleep 5
done
