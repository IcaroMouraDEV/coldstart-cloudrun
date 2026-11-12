"""Custo por etapa (pausa), região e cenário, a partir do faturamento exportado para o BigQuery.

Usa só os recursos faturados (vCPU-s ativos e de instância mínima, GiB-s e requisições) e os
preços públicos em USD da Tier 1 (us-central1, us-east1 e us-east4 têm o mesmo preço), sem a
franquia gratuita. A janela de cada etapa vai da primeira à última hora cheia com tráfego.

Também conta as instâncias distintas do cenário A em cada etapa (parquet do k6), que servem de
K_A para κ = ΔC_B / K_A no mesmo período.

Uso (com CLOUDSDK_CORE_PROJECT de infra/conta.env):
    python analysis/custo_etapas.py -o resultados/etapas
"""

import argparse
import os
from pathlib import Path

import pandas as pd

TABELA = "PROJETO.DATASET.gcp_billing_export_resource_v1_XXXXXX"  # sua tabela de exportação do faturamento

# pausa (min) -> (início, fim) em UTC, horas cheias, e parquets da etapa
ETAPAS = {
    30: ("2026-11-02T19:00:00Z", "2026-11-05T06:00:00Z", ["results/latencia-b48.parquet"]),
    15: ("2026-11-05T13:00:00Z", "2026-11-07T03:00:00Z", ["results/latencia-b913.parquet"]),
    60: ("2026-11-07T18:00:00Z", "2026-11-10T06:00:00Z",
         ["results/latencia-b14p.parquet", "results/latencia-b15p.parquet", "results/latencia-b16p.parquet"]),
}

PRECO = {"cpu": 0.000024, "cpu_min": 0.0000025, "mem": 0.0000025, "mem_min": 0.0000025, "req": 0.40e-6}

CONSULTA = """
SELECT location.region AS regiao,
  (SELECT value FROM UNNEST(labels) WHERE key = 'cenario') AS cenario,
  sku.description AS sku, SUM(usage.amount) AS uso
FROM `{tabela}`
WHERE service.description = 'Cloud Run'
  AND usage_start_time >= @inicio AND usage_start_time < @fim
GROUP BY regiao, cenario, sku
"""


def recurso(sku: str) -> str | None:
    s = sku.lower()
    if s.startswith("requests"):
        return "req"
    if "min instance cpu" in s:
        return "cpu_min"
    if "min instance memory" in s:
        return "mem_min"
    if "cpu" in s:
        return "cpu"
    if "memory" in s:
        return "mem"
    return None


def faturamento(inicio: str, fim: str) -> pd.DataFrame:
    """Consulta pela CLI `bq`, que respeita CLOUDSDK_CORE_ACCOUNT (o ADC pode estar em outra conta)."""
    import io
    import subprocess

    sql = CONSULTA.format(tabela=TABELA).replace("@inicio", f"TIMESTAMP '{inicio}'").replace("@fim", f"TIMESTAMP '{fim}'")
    saida = subprocess.run(["bq", "query", "--use_legacy_sql=false", "--format=csv", "--max_rows=10000", sql],
                           check=True, capture_output=True, text=True).stdout
    df = pd.read_csv(io.StringIO(saida))
    df = df[df["cenario"].isin(["a", "b", "c"])].assign(recurso=lambda d: d["sku"].map(recurso)).dropna(subset=["recurso"])
    df.loc[df["recurso"].str.startswith("mem"), "uso"] /= 2**30  # byte-s -> GiB-s
    return df.pivot_table(index=["regiao", "cenario"], columns="recurso", values="uso", aggfunc="sum", fill_value=0.0)


def instancias_a(parquets: list[str]) -> pd.Series:
    df = pd.concat([pd.read_parquet(p, columns=["regiao", "cenario", "servico", "instancia"]) for p in parquets])
    df = df[(df["cenario"] == "a") & df["instancia"].notna()]
    return df.groupby("regiao")["instancia"].nunique()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--saida", type=Path, default=Path("resultados/etapas"))
    args = parser.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)

    linhas = []
    for pausa, (inicio, fim, parquets) in ETAPAS.items():
        horas = (pd.Timestamp(fim) - pd.Timestamp(inicio)).total_seconds() / 3600
        uso = faturamento(inicio, fim)
        k_a = instancias_a(parquets)
        for c in PRECO:
            if c not in uso:
                uso[c] = 0.0
        uso["custo"] = sum(uso[c] * p for c, p in PRECO.items())
        for (regiao, cenario), r in uso.iterrows():
            linhas.append({"pausa_min": pausa, "inicio": inicio, "fim": fim, "horas": horas,
                           "regiao": regiao, "cenario": cenario, **r.to_dict(),
                           "custo_dia": r["custo"] * 24 / horas, "k_a": k_a.get(regiao)})
    t = pd.DataFrame(linhas)
    base = t[t["cenario"] == "a"].set_index(["pausa_min", "regiao"])["custo"]
    t["delta"] = t["custo"] - t.set_index(["pausa_min", "regiao"]).index.map(base)
    t["delta_pct"] = t["delta"] / t.set_index(["pausa_min", "regiao"]).index.map(base) * 100
    t["kappa"] = t["delta"] / t["k_a"]
    t.loc[t["cenario"] != "b", "kappa"] = None
    t.to_csv(args.saida / "custo_etapas.csv", index=False)
    pd.set_option("display.width", 220)
    print(t.drop(columns=["inicio", "fim"]).round(4).to_string(index=False))


if __name__ == "__main__":
    main()
