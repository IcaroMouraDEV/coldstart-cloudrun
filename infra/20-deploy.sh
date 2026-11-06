#!/usr/bin/env bash
# Implanta auth-<c> e backend-<c> de um cenário. Uso: [REGIAO=...] infra/20-deploy.sh <a|b|c>
# Os nomes dos serviços do Cloud Run são regionais: auth-a existe em cada região.
#   A: min-instances=0, sem CPU Boost
#   B: min-instances=1, sem CPU Boost
#   C: min-instances=0, com CPU Boost
set -euo pipefail
source "$(dirname "$0")/env.sh"
CENARIO="${1:-}"; valida_cenario "$CENARIO"
source "$INFRA_DIR/digests.env"
IMAGEM_AUTH="${REGISTRO}/auth@${DIGEST_AUTH}"
IMAGEM_BACKEND="${REGISTRO}/backend@${DIGEST_BACKEND}"

case "$CENARIO" in
  a) FLAGS_CENARIO=(--min-instances=0 --no-cpu-boost) ;;
  b) FLAGS_CENARIO=(--min-instances=1 --no-cpu-boost) ;;
  c) FLAGS_CENARIO=(--min-instances=0 --cpu-boost) ;;
esac

# Parâmetros fixos. --cpu-throttling mantém a cobrança por requisição:
# NÃO usar --no-cpu-throttling, que muda para cobrança por instância.
# --max-instances=2: a cota de CPU do Cloud Run do projeto (20 vCPU por região) é
# compartilhada pelos 6 serviços; com 10, A e C estouravam a cota ao partir juntos.
FLAGS_FIXAS=(
  --region="$REGIAO" --cpu=1 --memory=512Mi
  --concurrency=80 --max-instances=2 --timeout=60
  --execution-environment=gen2
  --cpu-throttling
  --service-account="$SA_SERVICOS"
  --no-allow-unauthenticated
  --quiet
)

deploy() {
  local servico=$1 imagem=$2 env=$3
  gcloud run deploy "${servico}-${CENARIO}" \
    --image="$imagem" \
    "${FLAGS_FIXAS[@]}" "${FLAGS_CENARIO[@]}" \
    --set-env-vars="$env" \
    --labels="cenario=${CENARIO},servico=${servico},${LABEL_EXPERIMENTO},${LABEL_REGIAO}"
  gcloud run services add-iam-policy-binding "${servico}-${CENARIO}" \
    --region="$REGIAO" --member="serviceAccount:${SA_GATEWAY}" \
    --role=roles/run.invoker --quiet >/dev/null
}

deploy auth "$IMAGEM_AUTH" "PROJETO=${PROJETO},SEGREDO_API_KEY=${SEGREDO_API_KEY}"
deploy backend "$IMAGEM_BACKEND" "PROJETO=${PROJETO},COLECAO=posts_${CENARIO},FIRESTORE_DB=${FIRESTORE_DB}"
