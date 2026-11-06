#!/usr/bin/env bash
# Cria (ou atualiza) o API Gateway de um cenário. Uso: [REGIAO=...] infra/30-gateway.sh <a|b|c>
# Cada cenário e região tem a sua API, config e gateway (gw-<c><sufixo>), gerados do mesmo template.
# APIs e configs são globais, por isso levam o sufixo da região; o gateway fica na própria região.
set -euo pipefail
source "$(dirname "$0")/env.sh"
CENARIO="${1:-}"; valida_cenario "$CENARIO"

API="coldstart-${CENARIO}${SUFIXO}"
GATEWAY="gw-${CENARIO}${SUFIXO}"
CONFIG="cfg-${CENARIO}${SUFIXO}-$(date +%Y%m%d-%H%M%S)"   # configs são imutáveis
LABELS="cenario=${CENARIO},servico=gateway,${LABEL_EXPERIMENTO},${LABEL_REGIAO}"

url_servico() {
  gcloud run services describe "$1" --region="$REGIAO" --format='value(status.url)'
}

export CENARIO
AUTH_URL=$(url_servico "auth-${CENARIO}"); export AUTH_URL
BACKEND_URL=$(url_servico "backend-${CENARIO}"); export BACKEND_URL

SPEC="$INFRA_DIR/gateway/openapi-${CENARIO}${SUFIXO}.yaml"
envsubst '${CENARIO} ${AUTH_URL} ${BACKEND_URL}' < "$INFRA_DIR/gateway/openapi.tmpl.yaml" > "$SPEC"

gcloud api-gateway apis describe "$API" >/dev/null 2>&1 ||
  gcloud api-gateway apis create "$API" --labels="$LABELS"

gcloud api-gateway api-configs create "$CONFIG" --api="$API" \
  --openapi-spec="$SPEC" \
  --backend-auth-service-account="$SA_GATEWAY" \
  --labels="$LABELS"

if gcloud api-gateway gateways describe "$GATEWAY" --location="$REGIAO" >/dev/null 2>&1; then
  gcloud api-gateway gateways update "$GATEWAY" --location="$REGIAO" \
    --api="$API" --api-config="$CONFIG"
else
  gcloud api-gateway gateways create "$GATEWAY" --location="$REGIAO" \
    --api="$API" --api-config="$CONFIG" --labels="$LABELS"
fi

# Sem isto, chamadas ao gateway falham com "API not enabled".
SERVICO_GERENCIADO=$(gcloud api-gateway apis describe "$API" --format='value(managedService)')
gcloud services enable "$SERVICO_GERENCIADO"

HOST=$(gcloud api-gateway gateways describe "$GATEWAY" --location="$REGIAO" --format='value(defaultHostname)')
echo "GATEWAY_${CENARIO^^}=https://${HOST}"
