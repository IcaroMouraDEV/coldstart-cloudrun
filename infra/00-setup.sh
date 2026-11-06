#!/usr/bin/env bash
# Preparação do projeto da GCP. Recursos globais uma vez; os regionais, por região.
# Uso: FIREBASE_WEB_API_KEY=... BILLING_ACCOUNT=... ORCAMENTO=800 MOEDA=BRL infra/00-setup.sh
#      REGIAO=us-east1 infra/00-setup.sh --regiao   # só os recursos da região (repositório e banco)
# MOEDA tem de ser a da conta de faturamento: gcloud billing accounts describe ID --format='value(currencyCode)'
set -euo pipefail
source "$(dirname "$0")/env.sh"

echo "== Artifact Registry e Firestore em ${REGIAO} (banco ${FIRESTORE_DB})"
gcloud artifacts repositories describe "$REPO" --location="$REGIAO" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REPO" --location="$REGIAO" \
    --repository-format=docker --labels="$LABEL_EXPERIMENTO"
gcloud firestore databases describe --database="$FIRESTORE_DB" >/dev/null 2>&1 ||
  gcloud firestore databases create --database="$FIRESTORE_DB" --location="$REGIAO" --type=firestore-native
[[ "${1:-}" == "--regiao" ]] && exit 0

: "${FIREBASE_WEB_API_KEY:?defina FIREBASE_WEB_API_KEY (Configurações do projeto no console do Firebase)}"

echo "== APIs"
gcloud services enable \
  run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  firestore.googleapis.com secretmanager.googleapis.com \
  apigateway.googleapis.com servicemanagement.googleapis.com servicecontrol.googleapis.com \
  compute.googleapis.com monitoring.googleapis.com logging.googleapis.com storage.googleapis.com \
  bigquery.googleapis.com billingbudgets.googleapis.com \
  identitytoolkit.googleapis.com firebase.googleapis.com

echo "== Contas de serviço"
for sa in sa-servicos gw-invoker sa-k6-vm; do
  gcloud iam service-accounts describe "${sa}@${PROJETO}.iam.gserviceaccount.com" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "$sa"
done
for papel in roles/datastore.user roles/secretmanager.secretAccessor roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJETO" --quiet --condition=None \
    --member="serviceAccount:${SA_SERVICOS}" --role="$papel" >/dev/null
done
# roles/run.invoker do gw-invoker é dado por serviço, em 20-deploy.sh

echo "== Bucket dos dados brutos (gs://${BUCKET})"
gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 ||
  gcloud storage buckets create "gs://${BUCKET}" --location="$REGIAO" \
    --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://${BUCKET}" --update-labels="$LABEL_EXPERIMENTO" >/dev/null
# a VM do k6 envia os resultados de cada bateria (criar e sobrescrever objetos, sem mexer em IAM)
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --quiet \
  --member="serviceAccount:${SA_K6}" --role=roles/storage.objectUser >/dev/null

echo "== Sink de logs do Cloud Run para o bucket (o _Default guarda só 30 dias)"
FILTRO_LOGS='resource.type="cloud_run_revision" AND resource.labels.service_name=~"^(auth|backend)-[abc]$"'
gcloud logging sinks describe "$SINK_LOGS" >/dev/null 2>&1 ||
  gcloud logging sinks create "$SINK_LOGS" "storage.googleapis.com/${BUCKET}" --log-filter="$FILTRO_LOGS"
ESCRITOR=$(gcloud logging sinks describe "$SINK_LOGS" --format='value(writerIdentity)')
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --quiet \
  --member="$ESCRITOR" --role=roles/storage.objectCreator >/dev/null

echo "== Secret Manager"
gcloud secrets describe "$SEGREDO_API_KEY" >/dev/null 2>&1 ||
  gcloud secrets create "$SEGREDO_API_KEY" --replication-policy=automatic --labels="$LABEL_EXPERIMENTO"
printf '%s' "$FIREBASE_WEB_API_KEY" | gcloud secrets versions add "$SEGREDO_API_KEY" --data-file=-

if [[ -n "${BILLING_ACCOUNT:-}" ]]; then
  echo "== Alerta de orçamento"
  gcloud billing budgets create --billing-account="$BILLING_ACCOUNT" \
    --display-name="coldstart-${PROJETO}" \
    --budget-amount="${ORCAMENTO:-50}${MOEDA:-USD}" \
    --filter-projects="projects/${PROJETO}" \
    --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
else
  echo "!! BILLING_ACCOUNT não definido: alerta de orçamento NÃO foi criado" >&2
fi

cat <<EOF

== Passos manuais (não há gcloud para eles)
1. Faturamento → Exportação do faturamento → BigQuery: ativar a exportação
   "Custo de uso detalhado" para um dataset (ex.: ${PROJETO}:faturamento).
   Faça isso ANTES da primeira bateria: a exportação não é retroativa.
2. Console do Firebase: adicionar o Firebase ao projeto ${PROJETO} e ativar o
   provedor "E-mail/senha" em Authentication.
EOF
