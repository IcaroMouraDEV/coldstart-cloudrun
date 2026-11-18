#!/usr/bin/env bash
# Roda N baterias de diagnóstico, cada uma depois de 30 min sem tráfego (o 1º ciclo também
# começa após uma pausa completa). Com AQUECER no config.env, o aquecedor de gateways roda
# durante toda a sequência, inclusive nas pausas, e o log dele vai junto para o bucket.
# Uso: load/run-diagnostico-baterias.sh [n=2]   (rode com setsid nohup; não depende do SSH)
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
N="${1:-2}"
set -a; source "$DIR/config.env"; set +a

AQ_PID=""
AQ_LOG="$(cd "$DIR/.." && pwd)/results/aquecimento-$(date +%Y%m%d).log"
if [[ -n "${AQUECER:-}" ]]; then
  mkdir -p "$(dirname "$AQ_LOG")"
  "$DIR/aquece-gateway.sh" "$AQ_LOG" &
  AQ_PID=$!
  echo "$(date -Is) aquecedor iniciado (pid $AQ_PID): $AQUECER a cada ${AQUECER_MIN:-5} min"
fi

for ((n = 1; n <= N; n++)); do
  echo "$(date -Is) pausa de 30 min antes da bateria d$n"; sleep 1800
  if "$DIR/run-diagnostico.sh" "$n"; then echo "$(date -Is) bateria d$n: ok"
  else echo "$(date -Is) bateria d$n: FALHOU"; fi
done

if [[ -n "$AQ_PID" ]]; then
  kill "$AQ_PID" 2>/dev/null || true
  gcloud storage cp "$AQ_LOG" "gs://${BUCKET}/diagnostico/${REGIAO}/" > /dev/null || true
fi
echo "$(date -Is) DIAGNOSTICO CONCLUIDO"
