"""Análises complementares pedidas na revisão do artigo de 10 páginas (baterias 1–3, us-central1).

- requisições de login excluídas, por cenário e nível;
- SLI principal e variantes: sem os descartes do k6, com limiar de 300 ms e de 1 s, com o login;
- IC 95% do SLI e do p99 por bootstrap de ciclos (ciclos inteiros, não requisições);
- instâncias distintas por reativação (o limite de 2 por serviço foi atingido?);
- requisições marcadas como frias com e sem o login (para conciliar com o Cloud Monitoring).

Uso: python analysis/revisao.py results/latencia-b123.parquet -o resultados/revisao
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from latencia import EXCLUIDAS, NIVEIS, SLO

REAMOSTRAS = 2000


def boa(df: pd.DataFrame, alvo_ms: float) -> pd.Series:
    return df["status"].between(200, 399) & (df["latencia_ms"] <= alvo_ms)


def variantes_sli(df: pd.DataFrame) -> pd.DataFrame:
    principal = df[~df["operacao"].isin(EXCLUIDAS)]
    sem_descartes = principal[principal["operacao"] != "descartada"]
    linhas = []
    for (cenario, nivel), g in principal.groupby(["cenario", "nivel"], observed=True):
        sd = sem_descartes[(sem_descartes["cenario"] == cenario) & (sem_descartes["nivel"] == nivel)]
        com_login = df[(df["cenario"] == cenario) & (df["nivel"] == nivel)]
        linhas.append({
            "cenario": cenario, "nivel": nivel,
            "requisicoes": len(g), "descartadas": int((g["operacao"] == "descartada").sum()),
            "sli_500": boa(g, 500).mean() * 100,
            "sli_500_sem_descartes": boa(sd, 500).mean() * 100,
            "sli_300": boa(g, 300).mean() * 100,
            "sli_1000": boa(g, 1000).mean() * 100,
            "sli_500_com_login": boa(com_login, 500).mean() * 100,
        })
    return pd.DataFrame(linhas)


def login_excluido(df: pd.DataFrame) -> pd.DataFrame:
    login = df[df["operacao"].isin(EXCLUIDAS)]
    g = login.groupby(["cenario", "nivel"], observed=True)
    return pd.DataFrame({
        "logins": g.size(),
        "falhas_401": g["status"].apply(lambda s: (s == 401).sum()),
        "atendidos_por_instancia": g["instancia"].apply(lambda s: s.notna().sum()),
    }).assign(taxa_falha=lambda t: t["falhas_401"] / t["logins"] * 100).reset_index()


def ic_ciclos(df: pd.DataFrame, semente: int = 42) -> pd.DataFrame:
    """Bootstrap de ciclos: reamostra os ciclos (bateria, ciclo) de cada nível com reposição."""
    principal = df[~df["operacao"].isin(EXCLUIDAS)].assign(boa=lambda d: boa(d, 500))
    rng = np.random.default_rng(semente)
    linhas = []
    for (cenario, nivel), g in principal.groupby(["cenario", "nivel"], observed=True):
        blocos = [b for _, b in g.groupby(["bateria", "ciclo"])]
        boas = np.array([b["boa"].sum() for b in blocos])
        totais = np.array([len(b) for b in blocos])
        lat = [b.loc[b["operacao"] != "descartada", "latencia_ms"].to_numpy() for b in blocos]
        slis, p99s = [], []
        for _ in range(REAMOSTRAS):
            idx = rng.integers(0, len(blocos), len(blocos))
            slis.append(boas[idx].sum() / totais[idx].sum() * 100)
            p99s.append(np.percentile(np.concatenate([lat[i] for i in idx]), 99))
        linhas.append({
            "cenario": cenario, "nivel": nivel, "ciclos": len(blocos),
            "sli": boas.sum() / totais.sum() * 100,
            "sli_ic_inf": np.percentile(slis, 2.5), "sli_ic_sup": np.percentile(slis, 97.5),
            "p99": np.percentile(np.concatenate(lat), 99),
            "p99_ic_inf": np.percentile(p99s, 2.5), "p99_ic_sup": np.percentile(p99s, 97.5),
        })
    return pd.DataFrame(linhas)


def instancias_por_reativacao(df: pd.DataFrame) -> pd.DataFrame:
    """Instâncias distintas que atenderam requisições em cada ciclo, por serviço (inclui o login)."""
    atendidas = df[df["instancia"].notna()]
    por_ciclo = (atendidas.groupby(["cenario", "nivel", "servico", "bateria", "ciclo"], observed=True)["instancia"]
                 .nunique().rename("instancias").reset_index())
    g = por_ciclo.groupby(["cenario", "nivel", "servico"], observed=True)["instancias"]
    return pd.DataFrame({
        "ciclos": g.size(),
        "ciclos_com_2": g.apply(lambda s: (s >= 2).sum()),
        "media": g.mean(),
        "maximo": g.max(),
    }).reset_index()


def frias_marcadas(df: pd.DataFrame) -> pd.DataFrame:
    frias = df[df["cold"] == "1"]
    g = frias.groupby(["cenario", "servico"], observed=True)
    return pd.DataFrame({
        "frias_com_login": g.size(),
        "frias_sem_login": g["operacao"].apply(lambda s: (~s.isin(EXCLUIDAS)).sum()),
        "primeira_foi_login": g["operacao"].apply(lambda s: s.isin(EXCLUIDAS).sum()),
    }).reset_index()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("entrada", type=Path)
    parser.add_argument("-o", "--saida", type=Path, default=Path("resultados/revisao"))
    args = parser.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)

    df = pd.read_parquet(args.entrada)
    df["nivel"] = pd.Categorical(df["nivel"], NIVEIS, ordered=True)
    tabelas = {
        "login_excluido": login_excluido(df),
        "sli_variantes": variantes_sli(df),
        "ic_ciclos": ic_ciclos(df),
        "instancias_reativacao": instancias_por_reativacao(df),
        "frias_marcadas": frias_marcadas(df),
    }
    pd.set_option("display.width", 200)
    for nome, t in tabelas.items():
        t.to_csv(args.saida / f"{nome}.csv", index=False)
        print(f"== {nome}\n{t.round(2).to_string(index=False)}\n")
    print(f"SLO de referência: {SLO}%")


if __name__ == "__main__":
    main()
