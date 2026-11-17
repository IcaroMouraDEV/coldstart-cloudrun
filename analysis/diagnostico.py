"""Teste de diagnóstico da penalidade do B (resultados/penalidade-b.md).

Lê os CSVs do k6 das baterias de diagnóstico (um por caminho: a, b, c, bt via gateway;
ad, bd, cd direto no Cloud Run) e compara, por caminho, as violações do SLO no início do
ciclo e no resto dele. Login fica de fora, como nas métricas principais.

Uso:
    python analysis/diagnostico.py results/diag/*-diag-d1 results/diag/*-diag-d2 -o resultados/diagnostico
"""

import argparse
from pathlib import Path

import pandas as pd

ALVO_MS = 500
CAMINHOS = {
    "a": "A via gateway", "ad": "A direto",
    "b": "B via gateway", "bd": "B direto", "bt": "B′ via gateway (CPU sempre alocada)",
    "c": "C via gateway", "cd": "C direto",
}


def tag(serie: pd.Series, nome: str) -> pd.Series:
    return serie.str.extract(rf"(?:^|&){nome}=([^&]*)")[0]


def carrega(pastas: list[Path]) -> pd.DataFrame:
    partes = []
    for pasta in pastas:
        for caminho in CAMINHOS:
            arq = pasta / f"{caminho}.csv.gz"
            if not arq.exists():
                continue
            d = pd.read_csv(arq, usecols=["metric_name", "timestamp", "metric_value", "status",
                                          "extra_tags", "metadata"], low_memory=False)
            d = d[d["metric_name"] == "latencia"]
            t = d["extra_tags"].fillna("")
            m = d["metadata"].fillna("")
            partes.append(pd.DataFrame({
                "caminho": caminho,
                "bateria": tag(t, "bateria").astype(int),
                "ciclo": tag(t, "ciclo").astype(int),
                "servico": tag(t, "servico"),
                "operacao": tag(t, "operacao"),
                "cold": tag(t, "cold"),
                "instante": pd.to_datetime(d["timestamp"], unit="ms", utc=True),
                "latencia_ms": d["metric_value"].astype(float),
                "status": pd.to_numeric(d["status"], errors="coerce").fillna(0).astype(int),
                "instancia": tag(m, "instancia"),
                "idade_ms": pd.to_numeric(tag(m, "idade_ms"), errors="coerce"),
            }))
    df = pd.concat(partes, ignore_index=True)
    df = df[df["operacao"] != "login"].copy()
    df["viol"] = (df["latencia_ms"] > ALVO_MS) | (df["status"] >= 400) | (df["status"] == 0)
    ini = df.groupby(["caminho", "bateria", "ciclo"])["instante"].transform("min")
    df["t"] = (df["instante"] - ini).dt.total_seconds()
    df = df.sort_values("instante")
    df["ordem"] = df.groupby(["caminho", "bateria", "ciclo", "servico"]).cumcount()
    return df


def resumo(df: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for caminho, g in df.groupby("caminho", sort=False):
        ini = g[g["t"] < 60]
        resto = g[g["t"] >= 60]
        primeiras = g[g["ordem"] < 10]
        afetadas = g[g["idade_ms"] < 10_000]
        linhas.append({
            "caminho": caminho, "descricao": CAMINHOS[caminho], "requisicoes": len(g),
            "sli": 100 * (1 - g["viol"].mean()),
            "viol_1min_pct": 100 * ini["viol"].mean(),
            "viol_resto_pct": 100 * resto["viol"].mean(),
            "p50_10primeiras_ms": primeiras["latencia_ms"].median(),
            "p90_10primeiras_ms": primeiras["latencia_ms"].quantile(0.9),
            "max_ms": g["latencia_ms"].max(),
            "instancias": g["instancia"].nunique(),
            "cold1": int((g["cold"] == "1").sum()),
            "viol_em_instancia_nova_pct": 100 * (g["viol"] & (g["idade_ms"] < 10_000)).sum() / max(g["viol"].sum(), 1),
            "afetadas": len(afetadas),
            "erros": int((g["status"] >= 400).sum() + (g["status"] == 0).sum()),
        })
    return pd.DataFrame(linhas)


def figura(r: pd.DataFrame, saida: Path) -> None:
    """Barras pareadas: % de violações no 1º minuto do ciclo, via gateway e direto, por cenário."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import numpy as np
    from graficos import CORES, TINTA, TINTA_2, plt, salva

    v = r.set_index("caminho")["viol_1min_pct"]
    grupos = [("A", "a", "ad"), ("B", "b", "bd"), ("B′\n(CPU sempre\nalocada)", "bt", None), ("C", "c", "cd")]
    fig, ax = plt.subplots(figsize=(4.6, 2.8))
    x = np.arange(len(grupos))
    larg = 0.36
    for i, (rotulo, gw, direto) in enumerate(grupos):
        cor = CORES[gw[0]]
        b1 = ax.bar(x[i] - (larg / 2 if direto else 0), v[gw], width=larg - 0.03, color=cor, zorder=2)
        ax.bar_label(b1, labels=[f"{v[gw]:.1f}".replace(".", ",")], padding=2, fontsize=7, color=TINTA_2)
        if direto:
            b2 = ax.bar(x[i] + larg / 2, v[direto], width=larg - 0.03, color="white", edgecolor=cor,
                        hatch="////", linewidth=1.2, zorder=2)
            ax.bar_label(b2, labels=[f"{v[direto]:.1f}".replace(".", ",")], padding=2, fontsize=7, color=TINTA_2)
    ax.set_xticks(x, [g[0] for g in grupos])
    ax.set_ylabel("Violações no 1º minuto do ciclo (%)")
    ax.yaxis.set_major_formatter(plt.matplotlib.ticker.FuncFormatter(lambda val, _: f"{val:g}".replace(".", ",")))
    ax.grid(axis="x", visible=False)
    ax.set_ylim(0, v.max() * 1.18)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor=TINTA_2, label="via API Gateway"),
                       Patch(facecolor="white", edgecolor=TINTA_2, hatch="////", label="direto no Cloud Run")],
              loc="upper left", fontsize=7.5)
    salva(fig, saida, "diagnostico_gateway")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pastas", nargs="+", type=Path)
    parser.add_argument("-o", "--saida", type=Path, default=Path("resultados/diagnostico"))
    args = parser.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)
    df = carrega(args.pastas)
    df.to_parquet(args.saida.parent.parent / "results" / "diagnostico.parquet", index=False)
    r = resumo(df)
    r.to_csv(args.saida / "resumo_caminhos.csv", index=False)
    por_ciclo = (df[df["t"] < 60].groupby(["caminho", "bateria", "ciclo"])["viol"].mean() * 100).rename("viol_1min_pct")
    por_ciclo.reset_index().to_csv(args.saida / "viol_1min_por_ciclo.csv", index=False)
    figura(r, args.saida)
    pd.set_option("display.width", 250)
    print(r.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
