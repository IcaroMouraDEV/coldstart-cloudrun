"""Tempo de inicialização das instâncias pelo Cloud Monitoring.

Lê a distribuição run.googleapis.com/container/startup_latencies de cada serviço
do experimento no período e grava média, contagem e percentis aproximados.

Uso: python analysis/startup.py --inicio 2026-11-02T08:00:00Z --fim 2026-11-02T20:00:00Z [--regioes us-east1]
Sem --regioes, consulta as regiões da campanha.
"""

import argparse
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from google.cloud import monitoring_v3

METRICA = "run.googleapis.com/container/startup_latencies"
REGIOES = ["us-central1", "us-east1", "us-east4"]


def percentil_histograma(limites: list[float], contagens: list[int], q: float) -> float:
    """Percentil aproximado pelo limite superior do bucket que contém o quantil."""
    acumulado = np.cumsum(contagens)
    if acumulado[-1] == 0:
        return float("nan")
    i = int(np.searchsorted(acumulado, q / 100 * acumulado[-1]))
    return limites[min(i, len(limites) - 1)] if limites else float("nan")


def limites_dos_buckets(opcoes) -> list[float]:
    if opcoes.explicit_buckets.bounds:
        return list(opcoes.explicit_buckets.bounds) + [float("inf")]
    exp = opcoes.exponential_buckets
    if exp.num_finite_buckets:
        return [exp.scale * exp.growth_factor ** i for i in range(exp.num_finite_buckets + 1)] + [float("inf")]
    lin = opcoes.linear_buckets
    return [lin.offset + lin.width * i for i in range(lin.num_finite_buckets + 1)] + [float("inf")]


def coleta(projeto: str, inicio: datetime, fim: datetime, regioes: list[str]) -> pd.DataFrame:
    cliente = monitoring_v3.MetricServiceClient()
    intervalo = monitoring_v3.TimeInterval(start_time=inicio, end_time=fim)
    series = cliente.list_time_series(request={
        "name": f"projects/{projeto}",
        "filter": f'metric.type = "{METRICA}" AND resource.labels.location = one_of({", ".join(f'"{r}"' for r in regioes)})',
        "interval": intervalo,
        "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
    })

    linhas = []
    for s in series:
        nome = s.resource.labels["service_name"]
        servico, _, cenario = nome.rpartition("-")
        if cenario not in ("a", "b", "c"):
            continue
        for ponto in s.points:
            d = ponto.value.distribution_value
            if d.count == 0:
                continue
            limites = limites_dos_buckets(d.bucket_options)
            # o Monitoring omite os buckets vazios do fim; completa até um bucket por limite
            contagens = list(d.bucket_counts) + [0] * (len(limites) - len(d.bucket_counts))
            linhas.append({
                "regiao": s.resource.labels["location"], "cenario": cenario, "servico": servico,
                "instante": ponto.interval.end_time, "inicializacoes": d.count,
                "media_ms": d.mean,
                "p50_ms": percentil_histograma(limites, contagens, 50),
                "p95_ms": percentil_histograma(limites, contagens, 95),
                "_limites": limites, "_contagens": contagens,
            })
    return pd.DataFrame(linhas)


def resumo(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega os histogramas de todos os pontos por região, cenário e serviço."""
    linhas = []
    for (regiao, cenario, servico), g in df.groupby(["regiao", "cenario", "servico"]):
        limites = g["_limites"].iloc[0]
        contagens = np.sum(np.vstack(g["_contagens"].to_numpy()), axis=0)
        n = g["inicializacoes"].sum()
        linhas.append({
            "regiao": regiao, "cenario": cenario, "servico": servico, "inicializacoes": n,
            "media_ms": (g["media_ms"] * g["inicializacoes"]).sum() / n,
            "p50_ms": percentil_histograma(limites, contagens, 50),
            "p95_ms": percentil_histograma(limites, contagens, 95),
            "p99_ms": percentil_histograma(limites, contagens, 99),
        })
    return pd.DataFrame(linhas)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inicio", required=True, type=datetime.fromisoformat)
    parser.add_argument("--fim", required=True, type=datetime.fromisoformat)
    parser.add_argument("--projeto")
    parser.add_argument("--regioes", nargs="+", default=REGIOES)
    parser.add_argument("-o", "--saida", type=Path, default=Path("results/startup"))
    args = parser.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)

    projeto = args.projeto or subprocess.check_output(
        ["gcloud", "config", "get-value", "project"], text=True).strip()
    df = coleta(projeto, args.inicio, args.fim, args.regioes)
    if df.empty:
        raise SystemExit("nenhum ponto da métrica no período")
    df.drop(columns=["_limites", "_contagens"]).to_csv(args.saida / "pontos.csv", index=False)
    tabela = resumo(df)
    tabela.to_csv(args.saida / "resumo.csv", index=False)
    print(tabela.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
