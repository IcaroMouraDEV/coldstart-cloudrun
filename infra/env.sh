# Configuração compartilhada pelos scripts de infra. Uso: source infra/env.sh
# Conta e projeto do TCC vêm de infra/conta.env (fora do git), não da configuração ativa do gcloud:
# a configuração pode mudar por fora e os comandos iriam para o projeto corporativo.
set -a; source "$(dirname "${BASH_SOURCE[0]}")/conta.env" || { echo "crie infra/conta.env (CLOUDSDK_CORE_ACCOUNT e CLOUDSDK_CORE_PROJECT)" >&2; exit 1; }; set +a

export PROJETO="$CLOUDSDK_CORE_PROJECT"
# Região do conjunto de recursos (serviços, gateways, VM, banco). Uso: REGIAO=us-east1 infra/...
# As três regiões da campanha são Tier 1 de preço do Cloud Run (mesma tabela de preços).
export REGIAO="${REGIAO:-us-central1}"
case "$REGIAO" in
  us-central1) ZONA_PADRAO=us-central1-a; SIGLA="" ;;   # nomes originais, sem sufixo
  us-east1)    ZONA_PADRAO=us-east1-b;    SIGLA="e1" ;;
  us-east4)    ZONA_PADRAO=us-east4-a;    SIGLA="e4" ;;
  *) echo "região não prevista: $REGIAO (use us-central1, us-east1 ou us-east4)" >&2; exit 1 ;;
esac
export ZONA="${ZONA:-$ZONA_PADRAO}" SIGLA
export SUFIXO="${SIGLA:+-$SIGLA}"   # para nomes globais: APIs do gateway, VM, banco
export REGIOES="us-central1 us-east1 us-east4"
export REGIAO_BUILD="us-central1"   # onde as imagens são construídas; as demais recebem cópias
export REPO="coldstart"
export REGISTRO="${REGIAO}-docker.pkg.dev/${PROJETO}/${REPO}"
# um banco Firestore por região, para o backend não cruzar regiões
if [[ -z "$SIGLA" ]]; then export FIRESTORE_DB="(default)"; else export FIRESTORE_DB="coldstart${SUFIXO}"; fi

export SA_SERVICOS="sa-servicos@${PROJETO}.iam.gserviceaccount.com"
export SA_GATEWAY="gw-invoker@${PROJETO}.iam.gserviceaccount.com"
export SA_K6="sa-k6-vm@${PROJETO}.iam.gserviceaccount.com"

export SEGREDO_API_KEY="firebase-web-api-key"
export VM_K6="k6-runner${SUFIXO}"
export LABEL_EXPERIMENTO="experimento=coldstart"
export LABEL_REGIAO="regiao=${REGIAO}"

# Bucket com os dados brutos (k6, logs exportados, métricas). Nomes de bucket são globais.
export BUCKET="${BUCKET:-${PROJETO}-coldstart-dados}"
export SINK_LOGS="coldstart-logs"

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export INFRA_DIR

valida_cenario() {
  case "$1" in
    a|b|c) ;;
    *) echo "cenário inválido: '$1' (use a, b ou c)" >&2; exit 1 ;;
  esac
}

if [[ -z "$PROJETO" ]]; then
  echo "defina PROJETO ou rode: gcloud config set project <id>" >&2
  exit 1
fi
