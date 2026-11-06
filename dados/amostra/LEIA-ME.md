# Amostra dos dados brutos

Os dados completos não são publicados. Esta amostra serve para rodar a análise de ponta a ponta e conferir o formato.

| Pasta | Origem | Formato do CSV |
|---|---|---|
| `20261102-b3/` | Bateria 3 do artigo (us-central1, pausa de 30 min) | Antigo: `instancia` e `idade_ms` em `extra_tags`, sem `regiao` nem `pausa` |
| `20261102-us-east1-p30-b4/` | Bateria 4 da campanha em 3 regiões (us-east1, pausa de 30 min) | Atual: `instancia` e `idade_ms` em `metadata` |

**O que tem em cada pasta:** um `<cenario>.csv.gz` por cenário (A, B e C), saída do k6 com `--out csv`. Ficam só os 3 primeiros ciclos, um de cada nível (2, 8 e 18 req/s), e as métricas que a análise lê: `latencia` (uma linha por requisição, com as tags do experimento) e `dropped_iterations`. São ~16,8 mil linhas por cenário.

Os resultados da amostra não substituem os do artigo, que somam três baterias de 18 ciclos.

```bash
analysis/.venv/bin/python analysis/carregar.py dados/amostra/*-b* -o results/latencia.parquet
analysis/.venv/bin/python analysis/latencia.py results/latencia.parquet -o results/tabelas --por-regiao
```
