#!/usr/bin/env bash
# Roda uma bateria: os três cenários em paralelo, um processo k6 por cenário, na VM do k6.
# Uso: load/run-bateria.sh <n> [--smoke]
#   --smoke: 2 ciclos de 2 min + 20 min parados, para validar roteiro, tags e coleta.
# Espera os três k6 terminarem e envia a pasta de resultados para gs://$BUCKET/baterias/<regiao>/.
# config.env define a região (REGIAO), o prefixo das contas (EMAIL_PREFIXO) e a pausa entre
# ciclos (PAUSA_CICLO_MIN, padrão 30). A CPU e a memória da VM vão para vmstat.log.
# Rode dentro de um tmux: os k6 sobrevivem à queda do SSH (setsid + nohup), mas o envio não.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

BATERIA="${1:-}"
[[ "$BATERIA" =~ ^[1-9][0-9]*$ ]] || { echo "uso: $0 <n> [--smoke]" >&2; exit 1; }

set -a; source "$DIR/config.env"; set +a
REGIAO="${REGIAO:-us-central1}"
EMAIL_PREFIXO="${EMAIL_PREFIXO:-k6}"
PAUSA_CICLO_MIN="${PAUSA_CICLO_MIN:-30}"

EXTRA=(-e PAUSA_MIN="$PAUSA_CICLO_MIN")
PREFIXO="b${BATERIA}"
if [[ "${2:-}" == "--smoke" ]]; then
  EXTRA=(-e CICLOS=2 -e TRAFEGO_MIN=2 -e PAUSA_MIN=20)
  PAUSA_CICLO_MIN=20
  PREFIXO="smoke${BATERIA}"
fi

SAIDA="$(cd "$DIR/.." && pwd)/results/$(date +%Y%m%d)-${REGIAO}-p${PAUSA_CICLO_MIN}-${PREFIXO}"
mkdir -p "$SAIDA"

# CPU (us/sy/id/st) e memória da VM a cada 30 s: mostra se o gerador de carga virou gargalo
vmstat -t -n 30 > "$SAIDA/vmstat.log" &
VMSTAT=$!
trap 'kill $VMSTAT 2>/dev/null || true' EXIT

declare -A PIDS
for c in a b c; do
  var="GATEWAY_${c^^}"
  K6_CSV_TIME_FORMAT=unix_milli setsid nohup k6 run --quiet \
    -e CENARIO="$c" -e BATERIA="$BATERIA" -e BASE_URL="${!var}" \
    -e REGIAO="$REGIAO" -e EMAIL_PREFIXO="$EMAIL_PREFIXO" \
    -e FIREBASE_API_KEY="$FIREBASE_API_KEY" -e SENHA_TESTE="$SENHA_TESTE" \
    "${EXTRA[@]}" \
    --out "csv=$SAIDA/${c}.csv.gz" \
    --summary-export="$SAIDA/${c}-resumo.json" \
    "$DIR/bateria.js" > "$SAIDA/${c}.log" 2>&1 < /dev/null &
  PIDS[$c]=$!
  echo "cenário $c: pid $!"
done

echo "$(date -Is) bateria $PREFIXO iniciada; saída em $SAIDA (acompanhe com: tail -f $SAIDA/*.log)"

FALHAS=0
for c in a b c; do
  codigo=0; wait "${PIDS[$c]}" || codigo=$?
  if ((codigo == 0)); then
    echo "$(date -Is) cenário $c terminou"
  else
    echo "$(date -Is) !! cenário $c terminou com código $codigo (veja $SAIDA/${c}.log)" >&2
    FALHAS=$((FALHAS + 1))
  fi
done

kill $VMSTAT 2>/dev/null || true

if [[ -n "${BUCKET:-}" ]]; then
  if gcloud storage cp -r "$SAIDA" "gs://${BUCKET}/baterias/${REGIAO}/"; then
    echo "$(date -Is) enviado para gs://${BUCKET}/baterias/${REGIAO}/$(basename "$SAIDA")/"
    [[ "${APAGAR_LOCAL:-0}" == "1" ]] && rm -rf "$SAIDA"
  else
    echo "$(date -Is) !! falha no envio; a cópia local ficou em $SAIDA" >&2
    FALHAS=$((FALHAS + 1))
  fi
else
  echo "!! BUCKET não definido em config.env: resultados só na VM ($SAIDA)" >&2
fi

exit $((FALHAS > 0))
