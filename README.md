# *Cold Start* no Google Cloud Run: latência × custo

Código para reproduzir o experimento do TCC que compara três formas de provisionar uma aplicação de microsserviços no Google Cloud Run sob tráfego intermitente. O experimento mede a latência de ponta a ponta, o cumprimento de um SLO (99% das requisições em até 500 ms) e o custo faturado.

| Cenário | Configuração |
|---|---|
| **A** | Escala a zero (`min-instances=0`) |
| **B** | Instância mínima ociosa (`min-instances=1`) |
| **C** | Escala a zero com *Startup CPU Boost* |

Os demais parâmetros são iguais nos três cenários: ambiente de 2ª geração, cobrança por requisição, 1 vCPU, 512 MiB e `max-instances=2`.

- **Artigo:** _referência a incluir_.
- **Dados brutos:** _DOI a incluir_. São as saídas do k6 de cada bateria, em CSV comprimido.

## Arquitetura

![Arquitetura](docs/arquitetura.png)

- **Ponto de entrada:** um API Gateway da GCP por cenário.
- **Serviços:** um Auth Service (FastAPI + Firebase Auth) e um Backend Service (FastAPI + Firestore), privados e invocados pelo gateway.
- **Instrumentação:** cada resposta traz `X-Cold-Start`, `X-Instance-Id` e `X-Instance-Age-Ms` (`services/comum/coldstart.py`), o que separa requisições frias, quentes e as que esperaram uma instância nova.
- **Carga:** o k6 roda em uma VM na mesma região, em modelo aberto (`constant-arrival-rate`).

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `services/` | Auth e Backend (FastAPI), Dockerfiles e testes |
| `infra/` | Scripts `gcloud` de preparação, *build*, *deploy*, gateways, VM do k6, *seed* e remoção |
| `load/` | Roteiro do k6 (`bateria.js`) e scripts de execução de baterias e campanhas |
| `analysis/` | Carga dos CSVs, latência e SLO, inicialização, custo, gráficos e testes |

## Protocolo de carga

Cada bateria tem 18 ciclos com 10 min de tráfego, intercalando 2, 8 e 18 req/s, seguidos de uma pausa sem tráfego. A pausa é o fator variado entre as etapas (`PAUSA_CICLO_MIN`). A mistura de requisições tem 30% de autenticação e 70% de CRUD de postagens (`load/bateria.js`). Os três cenários recebem a mesma carga ao mesmo tempo, com um processo k6 para cada um.

## Como reproduzir

**Pré-requisitos:** um projeto na GCP com faturamento, `gcloud` autenticado (`gcloud auth login` e `gcloud auth application-default login`), Docker e [uv](https://docs.astral.sh/uv/). O experimento gera custo: as campanhas do artigo custaram cerca de R$ 14 por bateria de 12 h, com as três configurações.

```bash
# 0. testes locais
(cd services && uv venv -p 3.12 && uv pip install -r requirements-dev.txt && .venv/bin/pytest tests)
(cd analysis && uv venv -p 3.12 && uv pip install -r requirements.txt && .venv/bin/pytest tests)

# 1. conta e projeto usados pelos scripts
cp infra/conta.env.exemplo infra/conta.env       # e preencha

# 2. projeto (uma vez); siga os passos manuais que o script imprime
FIREBASE_WEB_API_KEY=... BILLING_ACCOUNT=... ORCAMENTO=... MOEDA=BRL infra/00-setup.sh

# 3. imagens, cenários e gateways
infra/10-build.sh
for c in a b c; do infra/20-deploy.sh $c && infra/30-gateway.sh $c; done

# 4. dados iniciais e VM do k6
SENHA_TESTE=... uv run --with firebase-admin python infra/50-seed.py --usuarios 300
infra/40-vm-k6.sh            # copie load/ para a VM e crie load/config.env a partir de config.env.exemplo

# 5. baterias (na VM, dentro de um tmux, com o B ligado: infra/cenario-b.sh liga)
load/run-bateria.sh 1 --smoke           # bateria curta de validação
load/run-campanha.sh 1 5                # baterias em sequência, enviadas ao bucket

# 6. ao final
infra/cenario-b.sh desliga && infra/90-teardown.sh
```

Para rodar em outras regiões, use `REGIAO=us-east1 infra/<script>`. Cada região tem os próprios serviços, gateways, banco e VM (`infra/env.sh`).

## Como refazer a análise

A partir dos dados brutos (do bucket ou do pacote de dados publicado):

```bash
A=analysis/.venv/bin/python
$A analysis/carregar.py dados/*-b* -o results/latencia.parquet
$A analysis/latencia.py results/latencia.parquet -o results/tabelas --por-regiao   # SLI, percentis, IC, Mann-Whitney + Cliff, impacto do cold start
$A analysis/startup.py --inicio 2026-11-02T18:40:00Z --fim 2026-11-05T06:11:00Z    # tempo de inicialização (Cloud Monitoring do seu projeto)
$A analysis/custo.py results/tabelas/sli.csv --faturamento PROJETO.DATASET.TABELA --inicio AAAA-MM-DD --fim AAAA-MM-DD
$A analysis/graficos.py
```

**Decisões de análise:**
- **SLI:** os `POST /auth/login` ficam fora do SLI principal, e as iterações descartadas pelo k6 contam como violação.
- **Requisição afetada por *cold start*:** é a que chegou quando a instância tinha menos de 10 s de vida (idade na resposta − latência). `impacto_sensibilidade.csv` traz os cortes de 5, 10 e 30 s.

## Licença

O código está sob a licença MIT (`LICENSE`).
