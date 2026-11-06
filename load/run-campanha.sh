#!/usr/bin/env bash
# Roda várias baterias em sequência, com uma pausa sem tráfego entre elas.
# Uso: load/run-campanha.sh <primeira> <ultima> [pausa_min=30]
#   ex.: load/run-campanha.sh 1 28    # 14 dias de baterias de 12 h
# Uma bateria com falha não interrompe a campanha: fica registrada em results/campanha.log.
# Rode dentro de um tmux na VM.
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

PRIMEIRA="${1:-}"; ULTIMA="${2:-}"; PAUSA_MIN="${3:-30}"
[[ "$PRIMEIRA" =~ ^[1-9][0-9]*$ && "$ULTIMA" =~ ^[1-9][0-9]*$ && "$PRIMEIRA" -le "$ULTIMA" ]] ||
  { echo "uso: $0 <primeira> <ultima> [pausa_min]" >&2; exit 1; }

LOG="$DIR/../results/campanha.log"
mkdir -p "$(dirname "$LOG")"

for ((b = PRIMEIRA; b <= ULTIMA; b++)); do
  if "$DIR/run-bateria.sh" "$b" 2>&1 | tee -a "$LOG"; then
    echo "$(date -Is) bateria $b: ok" | tee -a "$LOG"
  else
    echo "$(date -Is) bateria $b: FALHOU" | tee -a "$LOG"
  fi
  set -a; source "$DIR/config.env"; set +a
  [[ -n "${BUCKET:-}" ]] && gcloud storage cp "$LOG" "gs://${BUCKET}/baterias/${REGIAO:-us-central1}/campanha.log" > /dev/null
  if ((b < ULTIMA)); then
    echo "$(date -Is) pausa de ${PAUSA_MIN} min sem tráfego" | tee -a "$LOG"
    sleep "$((PAUSA_MIN * 60))"
  fi
done
