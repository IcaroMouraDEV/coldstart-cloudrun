#!/usr/bin/env bash
# Constrói as imagens uma vez, copia para o registro de cada região e grava os digests em infra/digests.env.
# Os três cenários são implantados pelo digest, nunca pela tag.
set -euo pipefail
source "$(dirname "$0")/env.sh"

TAG="${TAG:-$(git -C "$INFRA_DIR" rev-parse --short HEAD)}"

[[ "$REGIAO" == "$REGIAO_BUILD" ]] || { echo "rode o build com REGIAO=$REGIAO_BUILD" >&2; exit 1; }
EXTRAS=""
for r in $REGIOES; do
  [[ "$r" == "$REGIAO_BUILD" ]] || EXTRAS+="${r}-docker.pkg.dev/${PROJETO}/${REPO} "
done

gcloud builds submit "$INFRA_DIR/../services" \
  --config="$INFRA_DIR/cloudbuild.yaml" \
  --substitutions="_REGISTRO=${REGISTRO},_TAG=${TAG},_REGISTROS_EXTRAS=${EXTRAS% }"

# Só o digest: 20-deploy.sh monta a imagem com o registro da própria região.
{
  for servico in auth backend; do
    digest=$(gcloud artifacts docker images describe "${REGISTRO}/${servico}:${TAG}" \
      --format='value(image_summary.digest)')
    for r in $REGIOES; do   # confere que a cópia de cada região tem o mesmo digest
      gcloud artifacts docker images describe "${r}-docker.pkg.dev/${PROJETO}/${REPO}/${servico}@${digest}" \
        >/dev/null || { echo "!! ${servico}@${digest} ausente em ${r}" >&2; exit 1; }
    done
    echo "DIGEST_${servico^^}=${digest}"
  done
} > "$INFRA_DIR/digests.env"

cat "$INFRA_DIR/digests.env"
