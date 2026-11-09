"""Figuras do artigo (PDF vetorial + PNG).

Uso:
    python analysis/graficos.py --tabelas results/tabelas --custo results/custo \
        --latencia results/latencia.parquet -o results/figuras

Com tabelas geradas por `latencia.py --por-regiao`, sai uma figura por região
(sufixo `_<regiao>`), além de `sli_por_nivel` com as regiões lado a lado e o
mapa `sli_mapa`.

Comparação entre etapas (pausas), em barras de % de violações:
    python analysis/graficos.py --etapas "15=results/tabelas-b913" "30=results/tabelas-b48" \
        -o results/figuras-etapas
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


def percentis_por_nivel(ic: pd.DataFrame, saida: Path, nome: str = "percentis_por_nivel") -> None:
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
    salva(fig, saida, nome)


def cdf_frias(lat: pd.DataFrame, saida: Path, nome: str = "cdf_frias_quentes") -> None:
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
    salva(fig, saida, nome)


def sli_por_nivel(sli: pd.DataFrame, saida: Path) -> None:
    """SLI por nível de carga e cenário (um painel por região, se houver), com a linha do SLO."""
    regioes = sorted(sli["regiao"].unique()) if "regiao" in sli else [None]
    fig, eixos = plt.subplots(1, len(regioes), figsize=(2.6 * len(regioes) + 0.6, 2.8),
                              sharey=True, squeeze=False)
    deslocamento = {"a": -0.18, "b": 0.0, "c": 0.18}
    x = np.arange(len(NIVEIS))
    for ax, regiao in zip(eixos[0], regioes):
        dados = sli if regiao is None else sli[sli["regiao"] == regiao]
        for c, g in dados.groupby("cenario"):
            g = g.set_index("nivel").reindex(NIVEIS)
            ax.plot(x + deslocamento[c], g["sli"], "o", markersize=5, color=CORES[c], label=NOMES[c])
        ax.axhline(SLO, color=TINTA_2, linewidth=1, linestyle=":")
        ax.set(title=regiao or "", xticks=x, xticklabels=["Baixo", "Médio", "Alto"],
               xlim=(-0.5, len(NIVEIS) - 0.5))
        ax.grid(axis="x", visible=False)
    eixos[0][0].set_ylabel("SLI (% em até 500 ms)")
    eixos[0][-1].annotate(f"SLO {SLO:.0f}%", (len(NIVEIS) - 0.6, SLO), xytext=(0, 3),
                          textcoords="offset points", ha="right", color=TINTA_2, fontsize=8)
    eixos[0][0].legend(loc="lower left", fontsize=7)
    salva(fig, saida, "sli_por_nivel")


def violacoes(sli: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    """% de requisições fora do SLO (100 − SLI), ponderado pelo volume de cada nível."""
    v = sli.assign(viol=sli["requisicoes"] * (100 - sli["sli"]) / 100)
    g = v.groupby(por)[["viol", "requisicoes"]].sum()
    return (100 * g["viol"] / g["requisicoes"]).rename("pct").reset_index()


def violacoes_por_pausa(etapas: dict[str, pd.DataFrame], saida: Path) -> None:
    """Barras agrupadas: % de violações por pausa e cenário, um painel por região, linha de 1%."""
    dados = pd.concat([violacoes(s, ["regiao", "cenario"]).assign(etapa=e) for e, s in etapas.items()])
    regioes = sorted(dados["regiao"].unique())
    rotulos = list(etapas)
    fig, eixos = plt.subplots(1, len(regioes), figsize=(2.7 * len(regioes) + 0.6, 3.0),
                              sharey=True, squeeze=False)
    largura, x = 0.26, np.arange(len(rotulos))
    deslocamento = {"a": -largura, "b": 0.0, "c": largura}
    teto = dados["pct"].max() * 1.18
    for ax, regiao in zip(eixos[0], regioes):
        d = dados[dados["regiao"] == regiao]
        for c in CORES:
            g = d[d["cenario"] == c].set_index("etapa").reindex(rotulos)
            barras = ax.bar(x + deslocamento[c], g["pct"], width=largura - 0.03, color=CORES[c],
                            label=NOMES[c], zorder=2)
            ax.bar_label(barras, labels=[f"{v:.1f}".replace(".", ",") for v in g["pct"]],
                         padding=2, fontsize=6.5, color=TINTA_2)
        ax.axhline(100 - SLO, color=TINTA, linewidth=1, linestyle=(0, (3, 2)), zorder=3)
        ax.set(title=regiao, xticks=x, xticklabels=rotulos, ylim=(0, teto))
        ax.set_xlabel("Pausa entre ciclos (min)")
        ax.grid(axis="x", visible=False)
    eixos[0][0].set_ylabel("Requisições fora do SLO (%)")
    eixos[0][0].annotate(f"limite do SLO ({100 - SLO:.0f}%)", (-0.5, 100 - SLO),
                         xytext=(2, 3), textcoords="offset points", ha="left", fontsize=7, color=TINTA)
    fig.legend(*eixos[0][0].get_legend_handles_labels(), loc="upper center", ncol=3,
               bbox_to_anchor=(0.5, 1.07), fontsize=7.5)
    salva(fig, saida, "violacoes_por_pausa")


def sli_mapa(sli: pd.DataFrame, saida: Path) -> None:
    """Mapa região × nível × cenário com o SLI escrito na célula; em destaque o que não cumpre."""
    nome = {"baixo": "Baixo", "medio": "Médio", "alto": "Alto"}
    sli = sli.assign(coluna=sli["cenario"].str.upper() + "\n" + sli["nivel"].map(nome))
    ordem = [f"{c.upper()}\n{nome[n]}" for c in CORES for n in NIVEIS]
    tabela = sli.pivot(index="regiao", columns="coluna", values="sli").reindex(columns=ordem)
    viol = 100 - tabela
    fig, ax = plt.subplots(figsize=(7, 0.55 * len(tabela) + 0.9))
    ax.imshow(viol.to_numpy(), cmap="Blues", vmin=0, vmax=max(2.0, float(np.nanmax(viol.to_numpy()))),
              aspect="auto")
    for i in range(tabela.shape[0]):
        for j in range(tabela.shape[1]):
            v = tabela.iat[i, j]
            if np.isnan(v):
                continue
            escuro = viol.iat[i, j] > 1.2
            ax.text(j, i, f"{v:.2f}".replace(".", ","), ha="center", va="center", fontsize=7.5,
                    color="white" if escuro else TINTA, fontweight="bold" if v < SLO else "normal")
    ax.set_xticks(range(len(ordem)), ordem, fontsize=7)
    ax.set_yticks(range(len(tabela)), tabela.index)
    for k in (3, 6):
        ax.axvline(k - 0.5, color="white", linewidth=3)
    ax.grid(False)
    ax.tick_params(length=0)
    for lado in ax.spines.values():
        lado.set_visible(False)
    ax.set_title(f"SLI (%); negrito = abaixo do SLO de {SLO:.0f}%", fontsize=8, color=TINTA_2, loc="left")
    salva(fig, saida, "sli_mapa")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tabelas", type=Path, default=Path("results/tabelas"))
    parser.add_argument("--custo", type=Path, default=Path("results/custo"))
    parser.add_argument("--latencia", type=Path, default=Path("results/latencia.parquet"))
    parser.add_argument("-o", "--saida", type=Path, default=Path("results/figuras"))
    parser.add_argument("--etapas", nargs="+", metavar="PAUSA=DIR_TABELAS",
                        help="só gera a comparação entre etapas, a partir do sli.csv de cada uma")
    args = parser.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)

    if args.etapas:
        etapas = {}
        for item in args.etapas:
            rotulo, pasta = item.split("=", 1)
            etapas[rotulo] = pd.concat(pd.read_csv(Path(p) / "sli.csv") for p in pasta.split(","))
        violacoes_por_pausa(etapas, args.saida)
        print(f"figuras em {args.saida}/")
        return

    ic = pd.read_csv(args.tabelas / "percentis_ic.csv")
    sli = pd.read_csv(args.tabelas / "sli.csv")
    sli_por_nivel(sli, args.saida)
    if "regiao" in sli:
        sli_mapa(sli, args.saida)
    lat = None
    if args.latencia.exists():
        colunas = ["cenario", "cold", "latencia_ms"] + (["regiao"] if "regiao" in ic else [])
        lat = pd.read_parquet(args.latencia, columns=colunas)
    if "regiao" in ic:
        for regiao, g in ic.groupby("regiao"):
            percentis_por_nivel(g, args.saida, f"percentis_por_nivel_{regiao}")
            if lat is not None:
                cdf_frias(lat[lat["regiao"].astype(str) == regiao], args.saida, f"cdf_frias_quentes_{regiao}")
    else:
        percentis_por_nivel(ic, args.saida)
        if lat is not None:
            cdf_frias(lat, args.saida)
    if (args.custo / "modelo.csv").exists():
        custo_por_volume(pd.read_csv(args.custo / "modelo.csv"),
                         json.loads((args.custo / "parametros.json").read_text()), args.saida)
    print(f"figuras em {args.saida}/")


if __name__ == "__main__":
    main()
