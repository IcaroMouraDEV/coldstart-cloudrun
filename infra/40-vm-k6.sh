#!/usr/bin/env bash
# Cria a VM geradora de carga (e2-medium: 1 vCPU compartilhada, 4 GB; mesma região) com o k6 instalado.
# Roda com a conta sa-k6-vm (00-setup.sh), que pode gravar no bucket dos dados brutos.
set -euo pipefail
source "$(dirname "$0")/env.sh"

gcloud compute instances create "$VM_K6" \
  --zone="$ZONA" --machine-type=e2-medium \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=10GB \
  --service-account="$SA_K6" --scopes=cloud-platform \
  --labels="servico=k6,${LABEL_EXPERIMENTO},${LABEL_REGIAO}" \
  --metadata=startup-script='#!/bin/bash
set -e
command -v k6 >/dev/null && exit 0
apt-get update
apt-get install -y gnupg ca-certificates curl
curl -fsSL https://dl.k6.io/key.gpg | gpg --dearmor -o /usr/share/keyrings/k6-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" > /etc/apt/sources.list.d/k6.list
apt-get update
apt-get install -y k6
'

cat <<EOF
VM criada. Para copiar o roteiro e entrar:
  gcloud compute scp --zone=$ZONA --recurse "$INFRA_DIR/../load" ${VM_K6}:~/
  gcloud compute ssh --zone=$ZONA ${VM_K6}
No load/config.env da VM, defina BUCKET=${BUCKET}, REGIAO=${REGIAO} e EMAIL_PREFIXO=k6${SUFIXO}
EOF
