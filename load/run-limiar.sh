#!/usr/bin/env bash
# Teste do limiar do API Gateway (resultados/limiar-gateway.md): a partir de quanto tempo parado
# o gateway passa a represar as requisições, e qual intervalo de aquecimento ainda o evita.
# Mesmo desenho do diagnóstico (2 baterias de 12 ciclos de 2 min a 8 req/s), mas cada caminho
# tem a sua pausa, que vale também antes do 1º ciclo (ATRASO_MIN), e todos rodam em paralelo.
# LIMIAR no config.env lista caminho:pausa_min, ex.: "b15:15 b20:20 b25:25 b30:30 bw10:30 bw15:30";
# AQUECER (ex.: "bw10:404:10 bw15:404:15") liga o aquecedor durante todo o teste.
# Uso: load/run-limiar.sh [--smoke]   (rode com setsid nohup; não depende do SSH)
# --smoke: 1 bateria de 2 ciclos de 1 min + 3 min parados, sem atraso, para validar os caminhos.
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
set -a; source "$DIR/config.env"; set +a
REGIAO="${REGIAO:-us-east1}"
EMAIL_PREFIXO="${EMAIL_PREFIXO:-k6}"
read -r -a ALVOS <<< "${LIMIAR:?defina LIMIAR no config.env}"

BATERIAS=2; CICLOS=12; TRAFEGO_MIN=2; ROT=limiar
if [[ "${1:-}" == "--smoke" ]]; then BATERIAS=1; CICLOS=2; TRAFEGO_MIN=1; ROT=limiar-smoke; fi
BASE="$(cd "$DIR/.." && pwd)/results/$(date +%Y%m%d)-${REGIAO}-${ROT}"
for ((n = 1; n <= BATERIAS; n++)); do mkdir -p "${BASE}-d$n"; done

vmstat -t -n 30 > "${BASE}-d1/vmstat.log" &
PIDS_AUX=($!)
if [[ -n "${AQUECER:-}" && "$ROT" == limiar ]]; then
  "$DIR/aquece-gateway.sh" "${BASE}-d1/aquecimento.log" &
  PIDS_AUX+=($!)
  echo "$(date -Is) aquecedor iniciado: $AQUECER"
fi
trap 'kill "${PIDS_AUX[@]}" 2>/dev/null || true' EXIT

caminho() {  # roda as baterias de um caminho em sequência
  local c=$1 pausa=$2 atraso=$2 n gw="GATEWAY_${1^^}"
  [[ "$ROT" == limiar-smoke ]] && { pausa=3; atraso=0; }
  for ((n = 1; n <= BATERIAS; n++)); do
    K6_CSV_TIME_FORMAT=unix_milli k6 run --quiet \
      -e CENARIO="$c" -e BATERIA="$n" -e BASE_URL="${!gw}" \
      -e REGIAO="$REGIAO" -e EMAIL_PREFIXO="$EMAIL_PREFIXO" \
      -e FIREBASE_API_KEY="$FIREBASE_API_KEY" -e SENHA_TESTE="$SENHA_TESTE" \
      -e CICLOS="$CICLOS" -e TRAFEGO_MIN="$TRAFEGO_MIN" -e PAUSA_MIN="$pausa" -e ATRASO_MIN="$atraso" \
      -e NIVEL=medio -e VUS_PRE=120 -e VUS_MAX=300 \
      --out "csv=${BASE}-d$n/${c}.csv.gz" \
      --summary-export="${BASE}-d$n/${c}-resumo.json" \
      "$DIR/bateria.js" > "${BASE}-d$n/${c}.log" 2>&1 < /dev/null || return 1
    echo "$(date -Is) caminho $c: bateria d$n ok"
  done
}

declare -A PIDS
for alvo in "${ALVOS[@]}"; do
  c="${alvo%%:*}"; p="${alvo##*:}"
  caminho "$c" "$p" &
  PIDS[$c]=$!
  echo "caminho $c (pausa $p min): pid $!"
done
echo "$(date -Is) teste $ROT iniciado; saída em ${BASE}-d*"

FALHAS=0
for c in "${!PIDS[@]}"; do
  if wait "${PIDS[$c]}"; then echo "$(date -Is) caminho $c terminou"
  else echo "$(date -Is) !! caminho $c falhou (ver ${BASE}-d*/${c}.log)" >&2; FALHAS=$((FALHAS + 1)); fi
done
kill "${PIDS_AUX[@]}" 2>/dev/null || true

if [[ -n "${BUCKET:-}" ]]; then
  for ((n = 1; n <= BATERIAS; n++)); do
    gcloud storage cp -r "${BASE}-d$n" "gs://${BUCKET}/diagnostico/${REGIAO}-${ROT}/" > /dev/null ||
      { echo "$(date -Is) !! falha no envio de ${BASE}-d$n" >&2; FALHAS=$((FALHAS + 1)); }
  done
  echo "$(date -Is) enviado para gs://${BUCKET}/diagnostico/${REGIAO}-${ROT}/"
fi
echo "$(date -Is) LIMIAR CONCLUIDO (falhas: $FALHAS)"
exit $((FALHAS > 0))
