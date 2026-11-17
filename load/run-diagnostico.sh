#!/usr/bin/env bash
# Teste de diagnóstico da penalidade do B (resultados/penalidade-b.md): sete caminhos em
# paralelo, um processo k6 por caminho, na VM do k6.
#   a, b, c   via API Gateway (GATEWAY_A, GATEWAY_B, GATEWAY_C)
#   bt        B' (CPU sempre alocada) via gateway (GATEWAY_BT)
#   ad, bd, cd  direto no Cloud Run, sem gateway (AUTH_URL_AD, BACKEND_URL_AD, ...)
# Uso: load/run-diagnostico.sh <n> [--smoke]
# Padrão: 12 ciclos de 2 min a 8 req/s (nível médio) + 30 min parados (~6,4 h).
# --smoke: 2 ciclos de 1 min + 3 min parados, para validar os caminhos.
# Envia a pasta para gs://$BUCKET/diagnostico/<regiao>/. Rode dentro de um tmux.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

BATERIA="${1:-}"
[[ "$BATERIA" =~ ^[1-9][0-9]*$ ]] || { echo "uso: $0 <n> [--smoke]" >&2; exit 1; }

set -a; source "$DIR/config.env"; set +a
REGIAO="${REGIAO:-us-east1}"
EMAIL_PREFIXO="${EMAIL_PREFIXO:-k6}"

CICLOS=12; TRAFEGO_MIN=2; PAUSA_MIN=30; PREFIXO="d${BATERIA}"
if [[ "${2:-}" == "--smoke" ]]; then
  CICLOS=2; TRAFEGO_MIN=1; PAUSA_MIN=3; PREFIXO="smoke-d${BATERIA}"
fi

SAIDA="$(cd "$DIR/.." && pwd)/results/$(date +%Y%m%d)-${REGIAO}-diag-${PREFIXO}"
mkdir -p "$SAIDA"
vmstat -t -n 30 > "$SAIDA/vmstat.log" &
VMSTAT=$!
trap 'kill $VMSTAT 2>/dev/null || true' EXIT

CAMINHOS=(a b c bt ad bd cd)
declare -A PIDS
for c in "${CAMINHOS[@]}"; do
  if [[ "$c" == ?d ]]; then
    au="AUTH_URL_${c^^}"; bu="BACKEND_URL_${c^^}"
    ALVO=(-e AUTH_URL="${!au}" -e BACKEND_URL="${!bu}")
  else
    gw="GATEWAY_${c^^}"
    ALVO=(-e BASE_URL="${!gw}")
  fi
  K6_CSV_TIME_FORMAT=unix_milli setsid nohup k6 run --quiet \
    -e CENARIO="$c" -e BATERIA="$BATERIA" "${ALVO[@]}" \
    -e REGIAO="$REGIAO" -e EMAIL_PREFIXO="$EMAIL_PREFIXO" \
    -e FIREBASE_API_KEY="$FIREBASE_API_KEY" -e SENHA_TESTE="$SENHA_TESTE" \
    -e CICLOS="$CICLOS" -e TRAFEGO_MIN="$TRAFEGO_MIN" -e PAUSA_MIN="$PAUSA_MIN" \
    -e NIVEL=medio -e VUS_PRE=120 -e VUS_MAX=300 \
    --out "csv=$SAIDA/${c}.csv.gz" \
    --summary-export="$SAIDA/${c}-resumo.json" \
    "$DIR/bateria.js" > "$SAIDA/${c}.log" 2>&1 < /dev/null &
  PIDS[$c]=$!
  echo "caminho $c: pid $!"
done
echo "$(date -Is) diagnóstico $PREFIXO iniciado; saída em $SAIDA"

FALHAS=0
for c in "${CAMINHOS[@]}"; do
  codigo=0; wait "${PIDS[$c]}" || codigo=$?
  if ((codigo == 0)); then echo "$(date -Is) caminho $c terminou"
  else echo "$(date -Is) !! caminho $c terminou com código $codigo" >&2; FALHAS=$((FALHAS + 1)); fi
done
kill $VMSTAT 2>/dev/null || true

if [[ -n "${BUCKET:-}" ]]; then
  if gcloud storage cp -r "$SAIDA" "gs://${BUCKET}/diagnostico/${REGIAO}/"; then
    echo "$(date -Is) enviado para gs://${BUCKET}/diagnostico/${REGIAO}/$(basename "$SAIDA")/"
  else
    echo "$(date -Is) !! falha no envio; cópia local em $SAIDA" >&2; FALHAS=$((FALHAS + 1))
  fi
fi
exit $((FALHAS > 0))
