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

# Variantes do teste de diagnóstico: ad/bd/cd são cópias de A/B/C acessadas direto (sem
# gateway); bt é o B com CPU sempre alocada (B'); aw/bw/br são A/B atrás de um gateway mantido
# aquecido (load/aquece-gateway.sh). Usam a coleção do cenário de origem.
THROTTLING=--cpu-throttling
case "$CENARIO" in
  a|ad|aw) FLAGS_CENARIO=(--min-instances=0 --no-cpu-boost) ;;
  b|bd|bw|br) FLAGS_CENARIO=(--min-instances=1 --no-cpu-boost) ;;
  bt) FLAGS_CENARIO=(--min-instances=1 --no-cpu-boost); THROTTLING=--no-cpu-throttling ;;
  c|cd) FLAGS_CENARIO=(--min-instances=0 --cpu-boost) ;;
esac
ORIGEM="${CENARIO:0:1}"

# Parâmetros fixos. --cpu-throttling mantém a cobrança por requisição:
# NÃO usar --no-cpu-throttling, que muda para cobrança por instância (só no B' do diagnóstico).
# --max-instances=2: a cota de CPU do Cloud Run do projeto (20 vCPU por região) é
# compartilhada pelos 6 serviços; com 10, A e C estouravam a cota ao partir juntos.
FLAGS_FIXAS=(
  --region="$REGIAO" --cpu=1 --memory=512Mi
  --concurrency=80 --max-instances=2 --timeout=60
  --execution-environment=gen2
  "$THROTTLING"
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
  if [[ "$CENARIO" == ?d ]]; then  # acesso direto: o k6 chama o serviço com o token da própria VM
    gcloud run services add-iam-policy-binding "${servico}-${CENARIO}" \
      --region="$REGIAO" --member="serviceAccount:${SA_K6}" \
      --role=roles/run.invoker --quiet >/dev/null
  fi
}

deploy auth "$IMAGEM_AUTH" "PROJETO=${PROJETO},SEGREDO_API_KEY=${SEGREDO_API_KEY}"
deploy backend "$IMAGEM_BACKEND" "PROJETO=${PROJETO},COLECAO=posts_${ORIGEM},FIRESTORE_DB=${FIRESTORE_DB}"
