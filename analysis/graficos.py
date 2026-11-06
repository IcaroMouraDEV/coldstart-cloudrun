"""Figuras do artigo (PDF vetorial + PNG).

Uso:
    python analysis/graficos.py --tabelas results/tabelas --custo results/custo \
        --latencia results/latencia.parquet -o results/figuras
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from latencia import ALVO_MS, NIVEIS, SLO

# Três primeiros slots da paleta categórica de referência (distinguíveis com daltonismo).
CORES = {"a": "#2a78d6", "b": "#eb6834", "c": "#1baf7a"}
NOMES = {"a": "A: Full Serverless", "b": "B: Idle Instance", "c": "C: CPU Boost"}
TINTA, TINTA_2, GRADE = "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({
    "font.size": 9, "axes.edgecolor": TINTA_2, "axes.labelcolor": TINTA,
    "xtick.color": TINTA_2, "ytick.color": TINTA_2, "text.color": TINTA,
    "axes.grid": True, "grid.color": GRADE, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False,
    "legend.frameon": False, "lines.linewidth": 2, "savefig.dpi": 200,
})


def salva(fig, saida: Path, nome: str) -> None:
    for ext in ("pdf", "png"):
        fig.savefig(saida / f"{nome}.{ext}", bbox_inches="tight")
    plt.close(fig)


def custo_por_volume(modelo: pd.DataFrame, parametros: dict, saida: Path) -> None:
    """Curvas C_i(N): trecho contínuo onde o cenário cumpre o SLO, tracejado onde não."""
    fig, ax = plt.subplots(figsize=(6, 3.6))
    n = modelo["volume_diario"]
    for c in CORES:
        if f"custo_{c}" not in modelo:
            continue
        custo = modelo[f"custo_{c}"]
        cumpre = modelo[f"sli_{c}"] >= SLO
        ax.plot(n, custo.where(~cumpre), color=CORES[c], linestyle=(0, (4, 3)), linewidth=1.5)
        ax.plot(n, custo.where(cumpre), color=CORES[c], label=NOMES[c])

    if parametros.get("N_s"):
        ax.axvline(parametros["N_s"], color=TINTA_2, linewidth=1, linestyle=":")
        ax.annotate(r"$N_s$", (parametros["N_s"], ax.get_ylim()[1]), xytext=(3, -10),
                    textcoords="offset points", color=TINTA_2)

    ax.set(xscale="log", yscale="log", xlabel="Requisições por dia (N)", ylabel="Custo diário (US$)")
    ax.plot([], [], color=TINTA_2, linestyle=(0, (4, 3)), linewidth=1.5, label="não cumpre o SLO")
    ax.legend(loc="upper left")
    salva(fig, saida, "custo_por_volume")


def percentis_por_nivel(ic: pd.DataFrame, saida: Path) -> None:
    """p50, p95 e p99 por nível de carga e cenário, com IC 95% por bootstrap."""
    fig, eixos = plt.subplots(1, 3, figsize=(7, 2.8), sharey=True)
    deslocamento = {"a": -0.18, "b": 0.0, "c": 0.18}
    x = np.arange(len(NIVEIS))
    for ax, q in zip(eixos, (50, 95, 99)):
        for c, g in ic.groupby("cenario"):
            g = g.set_index("nivel").reindex(NIVEIS)
            erro = [g[f"p{q}"] - g[f"p{q}_ic_inf"], g[f"p{q}_ic_sup"] - g[f"p{q}"]]
            ax.errorbar(x + deslocamento[c], g[f"p{q}"], yerr=erro, fmt="o", markersize=5,
                        color=CORES[c], elinewidth=1.2, capsize=0, label=NOMES[c])
        ax.axhline(ALVO_MS, color=TINTA_2, linewidth=1, linestyle=":")
        ax.set(title=f"p{q}", xticks=x, xticklabels=["Baixo", "Médio", "Alto"], yscale="log")
        ax.grid(axis="x", visible=False)
    eixos[0].set_ylabel("Latência (ms)")
    eixos[-1].annotate(f"alvo {ALVO_MS:.0f} ms", (len(NIVEIS) - 0.6, ALVO_MS), xytext=(0, 3),
                       textcoords="offset points", ha="right", color=TINTA_2, fontsize=8)
    for ax in eixos:
        ax.set_xlim(-0.5, len(NIVEIS) - 0.5)
    eixos[0].legend(loc="upper left", fontsize=7)
    salva(fig, saida, "percentis_por_nivel")


def cdf_frias(lat: pd.DataFrame, saida: Path) -> None:
    """Distribuição acumulada da latência das requisições frias e quentes, por cenário."""
    fig, eixos = plt.subplots(1, 2, figsize=(7, 2.8), sharex=True, sharey=True)
    for ax, (cold, titulo) in zip(eixos, (("1", "Requisições frias"), ("0", "Requisições quentes"))):
        for c, g in lat[lat["cold"] == cold].groupby("cenario", observed=True):
            v = np.sort(g["latencia_ms"].to_numpy())
            if len(v) == 0:
                continue
            ax.step(v, np.arange(1, len(v) + 1) / len(v), where="post", color=CORES[c],
                    label=f"{NOMES[c]} (n={len(v)})")
        ax.axvline(ALVO_MS, color=TINTA_2, linewidth=1, linestyle=":")
        ax.set(title=titulo, xscale="log", xlabel="Latência (ms)")
        ax.legend(loc="lower right", fontsize=7)
    eixos[0].set_ylabel("Fração acumulada")
    salva(fig, saida, "cdf_frias_quentes")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tabelas", type=Path, default=Path("results/tabelas"))
    parser.add_argument("--custo", type=Path, default=Path("results/custo"))
    parser.add_argument("--latencia", type=Path, default=Path("results/latencia.parquet"))
    parser.add_argument("-o", "--saida", type=Path, default=Path("results/figuras"))
    args = parser.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)

    percentis_por_nivel(pd.read_csv(args.tabelas / "percentis_ic.csv"), args.saida)
    if args.latencia.exists():
        cdf_frias(pd.read_parquet(args.latencia), args.saida)
    if (args.custo / "modelo.csv").exists():
        custo_por_volume(pd.read_csv(args.custo / "modelo.csv"),
                         json.loads((args.custo / "parametros.json").read_text()), args.saida)
    print(f"figuras em {args.saida}/")


if __name__ == "__main__":
    main()
