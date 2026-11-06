#!/usr/bin/env bash
# Liga ou desliga a instância mínima do cenário B entre baterias.
# Uso: [REGIAO=...] infra/cenario-b.sh <liga|desliga|status>
set -euo pipefail
source "$(dirname "$0")/env.sh"

case "${1:-}" in
  liga) MIN=1 ;;
  desliga) MIN=0 ;;
  status)
    for s in auth-b backend-b; do
      echo "$s: min-instances=$(gcloud run services describe "$s" --region="$REGIAO" \
        --format='value(spec.template.metadata.annotations."autoscaling.knative.dev/minScale")')"
    done
    exit 0 ;;
  *) echo "uso: $0 <liga|desliga|status>" >&2; exit 1 ;;
esac

for s in auth-b backend-b; do
  gcloud run services update "$s" --region="$REGIAO" --min-instances="$MIN" --quiet
done
