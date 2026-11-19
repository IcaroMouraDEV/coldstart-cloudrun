"""Teste de diagnóstico da penalidade do B (resultados/penalidade-b.md).

Lê os CSVs do k6 das baterias de diagnóstico (um por caminho: a, b, c, bt via gateway;
ad, bd, cd direto no Cloud Run) e compara, por caminho, as violações do SLO no início do
ciclo e no resto dele. Login fica de fora, como nas métricas principais.

Uso:
    python analysis/diagnostico.py results/diag/*-diag-d1 results/diag/*-diag-d2 -o resultados/diagnostico
"""

import argparse
import re
from pathlib import Path

import pandas as pd

ALVO_MS = 500
CAMINHOS = {
    "a": "A via gateway", "ad": "A direto",
    "b": "B via gateway", "bd": "B direto", "bt": "B′ via gateway (CPU sempre alocada)",
    "c": "C via gateway", "cd": "C direto",
    "aw": "A via gateway aquecido (404)", "bw": "B via gateway aquecido (404)",
    "br": "B via gateway aquecido (requisição real)",
    "b15": "B via gateway, 15 min parado", "b20": "B via gateway, 20 min parado",
    "b25": "B via gateway, 25 min parado", "b30": "B via gateway, 30 min parado",
    "bw10": "B via gateway aquecido (404 a cada 10 min)", "bw15": "B via gateway aquecido (404 a cada 15 min)",
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
                "regiao": re.match(r"\d{8}-([a-z]+-[a-z]+\d+)-", pasta.name).group(1),
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
    ini = df.groupby(["regiao", "caminho", "bateria", "ciclo"])["instante"].transform("min")
    df["t"] = (df["instante"] - ini).dt.total_seconds()
    df = df.sort_values("instante")
    df["ordem"] = df.groupby(["regiao", "caminho", "bateria", "ciclo", "servico"]).cumcount()
    return df


def resumo(df: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for (regiao, caminho), g in df.groupby(["regiao", "caminho"], sort=False):
        ini = g[g["t"] < 60]
        resto = g[g["t"] >= 60]
        primeiras = g[g["ordem"] < 10]
        afetadas = g[g["idade_ms"] < 10_000]
        linhas.append({
            "regiao": regiao, "caminho": caminho, "descricao": CAMINHOS[caminho], "requisicoes": len(g),
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


def _estilo():
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import graficos
    return graficos


def _rotulo(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


def figura(r: pd.DataFrame, saida: Path, nome: str = "diagnostico_gateway") -> None:
    """Barras pareadas (via gateway × direto) de % de violações no 1º minuto, um painel por região."""
    import numpy as np
    from matplotlib.patches import Patch
    g = _estilo()
    regioes = sorted(r["regiao"].unique())
    grupos = [("A", "a", "ad"), ("B", "b", "bd"), ("B′\n(CPU sempre\nalocada)", "bt", None), ("C", "c", "cd")]
    fig, eixos = g.plt.subplots(1, len(regioes), figsize=(3.3 * len(regioes) + 0.5, 2.9), sharey=True, squeeze=False)
    teto = r["viol_1min_pct"].max() * 1.18
    larg = 0.36
    for ax, regiao in zip(eixos[0], regioes):
        v = r[r["regiao"] == regiao].set_index("caminho")["viol_1min_pct"]
        x = np.arange(len(grupos))
        for i, (_, gw, direto) in enumerate(grupos):
            cor = g.CORES[gw[0]]
            b1 = ax.bar(x[i] - (larg / 2 if direto else 0), v[gw], width=larg - 0.03, color=cor, zorder=2)
            ax.bar_label(b1, labels=[_rotulo(v[gw])], padding=2, fontsize=6.5, color=g.TINTA_2)
            if direto:
                b2 = ax.bar(x[i] + larg / 2, v[direto], width=larg - 0.03, color="white", edgecolor=cor,
                            hatch="////", linewidth=1.2, zorder=2)
                ax.bar_label(b2, labels=[_rotulo(v[direto])], padding=2, fontsize=6.5, color=g.TINTA_2)
        ax.set_xticks(x, [gr[0] for gr in grupos], fontsize=7.5)
        ax.set(title=regiao, ylim=(0, teto))
        ax.grid(axis="x", visible=False)
    eixos[0][0].set_ylabel("Violações no 1º minuto do ciclo (%)")
    eixos[0][0].yaxis.set_major_formatter(g.matplotlib.ticker.FuncFormatter(lambda val, _: f"{val:g}".replace(".", ",")))
    fig.legend(handles=[Patch(facecolor=g.TINTA_2, label="via API Gateway"),
                        Patch(facecolor="white", edgecolor=g.TINTA_2, hatch="////", label="direto no Cloud Run")],
               loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.07), fontsize=7.5)
    g.salva(fig, saida, nome)


def figura_aquecimento(r: pd.DataFrame, saida: Path, nome: str = "aquecimento_gateway") -> None:
    """Barras: % de violações no 1º minuto com o gateway frio e aquecido (404 ou requisição real)."""
    import numpy as np
    from matplotlib.patches import Patch
    g = _estilo()
    v = r.set_index("caminho")["viol_1min_pct"]
    barras = [("a", "A", None), ("aw", "A", "404"), ("b", "B", None), ("bw", "B", "404"), ("br", "B", "real")]
    fig, ax = g.plt.subplots(figsize=(4.4, 2.8))
    x = np.array([0, 0.42, 1.25, 1.67, 2.09])
    for xi, (c, cen, aq) in zip(x, barras):
        cor = g.CORES[c[0]]
        kw = dict(color=cor) if aq is None else dict(color="white", edgecolor=cor, hatch="////" if aq == "404" else "....", linewidth=1.2)
        b = ax.bar(xi, v[c], width=0.38, zorder=2, **kw)
        ax.bar_label(b, labels=[_rotulo(v[c])], padding=2, fontsize=7, color=g.TINTA_2)
    ax.set_xticks(x, ["A", "A", "B", "B", "B"])
    ax.set_ylabel("Violações no 1º minuto do ciclo (%)")
    ax.yaxis.set_major_formatter(g.matplotlib.ticker.FuncFormatter(lambda val, _: f"{val:g}".replace(".", ",")))
    ax.grid(axis="x", visible=False)
    ax.set_ylim(0, v.max() * 1.2)
    ax.legend(handles=[Patch(facecolor=g.TINTA_2, label="gateway sem aquecimento"),
                       Patch(facecolor="white", edgecolor=g.TINTA_2, hatch="////", label="aquecido com 404 a cada 5 min"),
                       Patch(facecolor="white", edgecolor=g.TINTA_2, hatch="....", label="aquecido com requisição real")],
              loc="upper right", fontsize=7)
    g.salva(fig, saida, nome)


def figura_limiar(r: pd.DataFrame, por_ciclo: pd.DataFrame, saida: Path, nome: str = "limiar_gateway") -> None:
    """Violações no 1º minuto por tempo parado (b15..b30) e com aquecimento a cada 15 e 10 min, um painel por região.
    Barra = média dos 24 ciclos; pontos = cada ciclo."""
    import numpy as np
    from matplotlib.patches import Patch
    g = _estilo()
    regioes = sorted(r["regiao"].unique())
    barras = [("b15", "15"), ("b20", "20"), ("b25", "25"), ("b30", "30"), ("bw15", "30\n404/15"), ("bw10", "30\n404/10")]
    x = np.array([0, 1, 2, 3, 4.3, 5.3])
    fig, eixos = g.plt.subplots(1, len(regioes), figsize=(3.4 * len(regioes) + 0.5, 3.0), sharey=True, squeeze=False)
    for ax, regiao in zip(eixos[0], regioes):
        v = r[r["regiao"] == regiao].set_index("caminho")["viol_1min_pct"]
        pcr = por_ciclo[por_ciclo["regiao"] == regiao]
        for xi, (c, _) in zip(x, barras):
            kw = dict(color=g.CORES["b"]) if not c.startswith("bw") else dict(color="white", edgecolor=g.CORES["b"], hatch="////", linewidth=1.2)
            b = ax.bar(xi, v[c], width=0.6, zorder=2, **kw)
            ax.bar_label(b, labels=[_rotulo(v[c])], padding=2, fontsize=6.5, color=g.TINTA_2)
            pc = pcr[pcr["caminho"] == c]["viol_1min_pct"]
            ax.scatter(np.full(len(pc), xi) + np.linspace(-0.2, 0.2, len(pc)), pc, s=5, color=g.TINTA_2, alpha=0.45, zorder=3, linewidths=0)
        ax.set_xticks(x, [b[1] for b in barras], fontsize=7)
        ax.set_title(regiao)
        ax.grid(axis="x", visible=False)
    eixos[0][len(regioes) // 2].set_xlabel("Minutos parados antes do ciclo (B via API Gateway)")
    eixos[0][0].set_ylabel("Violações no 1º minuto do ciclo (%)")
    eixos[0][0].yaxis.set_major_formatter(g.matplotlib.ticker.FuncFormatter(lambda val, _: f"{val:g}".replace(".", ",")))
    fig.legend(handles=[Patch(facecolor=g.CORES["b"], label="sem aquecimento"),
                        Patch(facecolor="white", edgecolor=g.CORES["b"], hatch="////", label="404 a cada 15 ou 10 min"),
                        g.plt.Line2D([], [], marker="o", ls="", color=g.TINTA_2, alpha=0.6, markersize=3, label="um ciclo")],
               loc="upper center", ncol=3, bbox_to_anchor=(0.5, 1.07), fontsize=7.5)
    g.salva(fig, saida, nome)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pastas", nargs="+", type=Path)
    parser.add_argument("-o", "--saida", type=Path, default=Path("resultados/diagnostico"))
    args = parser.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)
    df = carrega(args.pastas)
    df.to_parquet(Path("results") / f"{args.saida.name}.parquet", index=False)
    r = resumo(df)
    r.to_csv(args.saida / "resumo_caminhos.csv", index=False)
    por_ciclo = (df[df["t"] < 60].groupby(["regiao", "caminho", "bateria", "ciclo"])["viol"].mean() * 100).rename("viol_1min_pct")
    por_ciclo.reset_index().to_csv(args.saida / "viol_1min_por_ciclo.csv", index=False)
    completas = [reg for reg, rr in r.groupby("regiao") if {"a", "ad", "b", "bd", "bt", "c", "cd"} <= set(rr["caminho"])]
    if completas:
        figura(r[r["regiao"].isin(completas)], args.saida)
    if {"a", "aw", "b", "bw", "br"} <= set(r["caminho"]):
        figura_aquecimento(r, args.saida)
    if {"b15", "b20", "b25", "b30", "bw10", "bw15"} <= set(r["caminho"]):
        figura_limiar(r, por_ciclo.reset_index(), args.saida)
    pd.set_option("display.width", 250)
    print(r.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
