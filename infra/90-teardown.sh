#!/usr/bin/env bash
# Uso: [REGIAO=...] infra/90-teardown.sh [--imagens]
# Remove os recursos do experimento: gateways, serviços, VM e, com --imagens, as imagens.
# Mantém Firestore, segredos, contas de serviço, a exportação do faturamento e o
# bucket dos dados brutos, necessários para a análise, que chega com atraso.
set -euo pipefail
source "$(dirname "$0")/env.sh"

read -r -p "Apagar os recursos do experimento no projeto ${PROJETO}? [s/N] " ok
[[ "$ok" == "s" ]] || exit 1

for c in a b c; do
  gcloud api-gateway gateways delete "gw-${c}${SUFIXO}" --location="$REGIAO" --quiet || true
  for cfg in $(gcloud api-gateway api-configs list --api="coldstart-${c}${SUFIXO}" --format='value(name.basename())' 2>/dev/null); do
    gcloud api-gateway api-configs delete "$cfg" --api="coldstart-${c}${SUFIXO}" --quiet || true
  done
  gcloud api-gateway apis delete "coldstart-${c}${SUFIXO}" --quiet || true
  for s in auth backend; do
    gcloud run services delete "${s}-${c}" --region="$REGIAO" --quiet || true
  done
done

gcloud compute instances delete "$VM_K6" --zone="$ZONA" --quiet || true
# sem serviços não há mais logs a exportar; os já exportados ficam no bucket
gcloud logging sinks delete "$SINK_LOGS" --quiet || true

if [[ "${1:-}" == "--imagens" ]]; then
  gcloud artifacts repositories delete "$REPO" --location="$REGIAO" --quiet || true
fi

echo "Serviços restantes com label ${LABEL_EXPERIMENTO}:"
gcloud run services list --region="$REGIAO" --filter="metadata.labels.experimento=coldstart" --format='value(metadata.name)'
