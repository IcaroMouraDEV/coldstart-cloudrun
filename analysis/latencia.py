"""Latência e SLO: percentis, SLI, Mann-Whitney + delta de Cliff,
IC 95% por bootstrap e coeficiente de variação entre baterias.

Uso: python analysis/latencia.py results/latencia.parquet -o results/tabelas
"""

import argparse
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ALVO_MS = 500.0
SLO = 99.0
ALFA = 0.05
NIVEIS = ["baixo", "medio", "alto"]
TAXAS = {"baixo": 2, "medio": 8, "alto": 18}  # req/s, load/bateria.js
# POST /auth/login fica fora das métricas principais: o Firebase Auth passa a recusar o
# signInWithPassword (401 no serviço) após ~2 min a ~5 logins/s, igual nos três cenários.
# É um limite externo, não efeito do provisionamento; vai à parte em login.csv.
EXCLUIDAS = ["login"]
# Requisição "afetada por cold start": chegou quando a instância tinha menos de LIMIAR_AFETADA_MS
# de vida (idade na chegada = X-Instance-Age-Ms - latência; negativa se chegou antes de ela nascer).
# Inclui as que esperaram na fila da instância que inicializava, marcadas como quentes. A idade na
# resposta, usada até 05/11, subestimava o efeito em partidas lentas: uma requisição que esperou 20 s
# era respondida por uma instância de 13 s e contava como não afetada.
LIMIAR_AFETADA_MS = 10_000
LIMIARES_SENSIBILIDADE_MS = [5_000, 10_000, 30_000]


def boa(df: pd.DataFrame) -> pd.Series:
    """Requisição dentro do SLO: sem erro e até o alvo. Erros e timeouts (status 0) violam."""
    return (df["status"].between(200, 399)) & (df["latencia_ms"] <= ALVO_MS)


def sli(df: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    g = df.assign(boa=boa(df)).groupby(por, observed=True)
    return pd.DataFrame({
        "requisicoes": g.size(),
        "frias": g["cold"].apply(lambda s: (s == "1").sum()),
        "erros": g["status"].apply(lambda s: (~s.between(200, 399)).sum()),
        "descartadas": g["operacao"].apply(lambda s: (s == "descartada").sum()) if "operacao" in df else 0,
        "sli": g["boa"].mean() * 100,
    }).assign(cumpre_slo=lambda t: t["sli"] >= SLO)


def percentis(df: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    g = df.groupby(por, observed=True)["latencia_ms"]
    return pd.DataFrame({
        "n": g.size(),
        "p50": g.median(),
        "p95": g.quantile(0.95),
        "p99": g.quantile(0.99),
        "max": g.max(),
    })


def ic_bootstrap(valores: np.ndarray, q: float, reamostras: int = 1000, semente: int = 42) -> tuple[float, float]:
    if len(valores) < 2:
        return (np.nan, np.nan)
    res = stats.bootstrap(
        (valores,), lambda x, axis: np.percentile(x, q, axis=axis),
        n_resamples=reamostras, batch=100, confidence_level=0.95, method="percentile",
        random_state=np.random.default_rng(semente),
    )
    return res.confidence_interval.low, res.confidence_interval.high


def percentis_com_ic(df: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    linhas = []
    for chave, grupo in df.groupby(por, observed=True):
        v = grupo["latencia_ms"].to_numpy()
        linha = dict(zip(por, chave if isinstance(chave, tuple) else (chave,)))
        for q in (50, 95, 99):
            baixo, alto = ic_bootstrap(v, q)
            linha |= {f"p{q}": np.percentile(v, q), f"p{q}_ic_inf": baixo, f"p{q}_ic_sup": alto}
        linhas.append(linha)
    return pd.DataFrame(linhas)


def delta_cliff(u: float, n1: int, n2: int) -> float:
    """Delta de Cliff a partir do U de Mann-Whitney: P(X > Y) − P(X < Y)."""
    return 2 * u / (n1 * n2) - 1


def magnitude_cliff(d: float) -> str:
    # limiares de Romano et al. (2006)
    d = abs(d)
    return "desprezível" if d < 0.147 else "pequeno" if d < 0.33 else "médio" if d < 0.474 else "grande"


def comparacoes(df: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    """Mann-Whitney bilateral entre pares de cenários, dentro de cada grupo de `por`."""
    linhas = []
    for chave, grupo in df.groupby(por, observed=True):
        amostras = {c: g["latencia_ms"].to_numpy() for c, g in grupo.groupby("cenario", observed=True)}
        for x, y in combinations(sorted(amostras), 2):
            a, b = amostras[x], amostras[y]
            if len(a) == 0 or len(b) == 0:
                continue
            res = stats.mannwhitneyu(a, b, alternative="two-sided")
            d = delta_cliff(res.statistic, len(a), len(b))
            linhas.append({
                **dict(zip(por, chave if isinstance(chave, tuple) else (chave,))),
                "par": f"{x}×{y}", "u": res.statistic, "p_valor": res.pvalue,
                "significativo": res.pvalue < ALFA, "delta_cliff": d, "magnitude": magnitude_cliff(d),
            })
    return pd.DataFrame(linhas)


def cv_baterias(df: pd.DataFrame, extra: list[str] | None = None) -> pd.DataFrame:
    """Coeficiente de variação (%) de p95, p99 e SLI entre as baterias.

    Uma bateria é identificada por (regiao, bateria): os números se repetem entre regiões.
    """
    extra = extra or []
    chave = extra + ["cenario", "nivel", "regiao", "bateria"]
    chave = list(dict.fromkeys(chave))
    por_bateria = percentis(df, chave).join(sli(df, chave)["sli"])
    g = por_bateria.groupby(level=extra + ["cenario", "nivel"], observed=True)[["p95", "p99", "sli"]]
    return (g.std(ddof=1) / g.mean() * 100).add_prefix("cv_")


def com_idade_na_chegada(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["operacao"] != "descartada"].assign(
        viola=lambda x: ~boa(x), chegada_ms=lambda x: x["idade_ms"] - x["latencia_ms"])


def impacto_cold(df: pd.DataFrame, limiar_ms: float = LIMIAR_AFETADA_MS, por: list[str] | None = None) -> pd.DataFrame:
    """Quanto das violações vem de instâncias recém-iniciadas, além das marcadas com cold=1.

    Três categorias de violação: afetada, não afetada e sem instância (resposta sem idade,
    como um erro de espera por instância). Sem instância não entra em sli_nao_afetadas.
    `_resposta` é o critério anterior (idade na resposta), mantido para comparação.
    """
    d = com_idade_na_chegada(df).assign(
        afetada=lambda x: x["chegada_ms"] < limiar_ms, sem_idade=lambda x: x["idade_ms"].isna(),
        fria=lambda x: x["cold"] == "1")
    d = d.assign(viola_afetada=d["viola"] & d["afetada"], viola_fria=d["viola"] & d["fria"],
                 viola_sem_instancia=d["viola"] & d["sem_idade"],
                 viola_afetada_resposta=d["viola"] & (d["idade_ms"] < limiar_ms),
                 boa_nao_afetada=(~d["viola"]).where(~d["afetada"] & ~d["sem_idade"]).astype(float))
    g = d.groupby((por or []) + ["cenario", "nivel"], observed=True)
    t = pd.DataFrame({
        "requisicoes": g.size(),
        "frias_marcadas": g["fria"].sum(),
        "afetadas": g["afetada"].sum(),
        "violacoes": g["viola"].sum(),
        "violacoes_afetadas": g["viola_afetada"].sum(),
        "violacoes_sem_instancia": g["viola_sem_instancia"].sum(),
        "violacoes_frias_marcadas": g["viola_fria"].sum(),
        "violacoes_afetadas_resposta": g["viola_afetada_resposta"].sum(),
        "sli_nao_afetadas": g["boa_nao_afetada"].mean() * 100,  # NaN (afetadas) fica fora da média
    })
    violacoes = t["violacoes"].where(t["violacoes"] > 0)
    return t.assign(
        afetadas_por_fria=lambda t: t["afetadas"] / t["frias_marcadas"].where(t["frias_marcadas"] > 0),
        pct_violacoes_afetadas=lambda t: t["violacoes_afetadas"] / violacoes * 100,
        pct_violacoes_afetadas_resposta=lambda t: t["violacoes_afetadas_resposta"] / violacoes * 100,
    )


def impacto_sensibilidade(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """Percentual de violações atribuídas a cold start em cada corte, somando os níveis."""
    d = com_idade_na_chegada(df)
    d = d[d["viola"]]
    g = d.groupby((por or []) + ["cenario"], observed=True)
    t = pd.DataFrame({"violacoes": g.size()})
    for limiar in LIMIARES_SENSIBILIDADE_MS:
        s = limiar // 1000
        t[f"pct_chegada_{s}s"] = d.assign(m=d["chegada_ms"] < limiar).groupby(g.keys, observed=True)["m"].mean() * 100
        t[f"pct_resposta_{s}s"] = d.assign(m=d["idade_ms"] < limiar).groupby(g.keys, observed=True)["m"].mean() * 100
    t["pct_sem_instancia"] = d.assign(m=d["idade_ms"].isna()).groupby(g.keys, observed=True)["m"].mean() * 100
    return t


def falhas_login(df: pd.DataFrame) -> pd.DataFrame:
    chave = [c for c in ["pausa", "regiao"] if c in df] + ["cenario", "nivel", "bateria"]
    g = df[df["operacao"] == "login"].groupby(chave, observed=True)["status"]
    return pd.DataFrame({"logins": g.size(), "falhas_401": g.apply(lambda s: (s == 401).sum())}).assign(
        taxa_falha=lambda t: t["falhas_401"] / t["logins"] * 100)


def volume_diario(nivel: str) -> int:
    return TAXAS[nivel] * 86_400


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("entrada", type=Path)
    parser.add_argument("-o", "--saida", type=Path, default=Path("results/tabelas"))
    parser.add_argument("--com-login", action="store_true", help="não exclui POST /auth/login")
    parser.add_argument("--por-regiao", action="store_true", help="separa as tabelas por região")
    parser.add_argument("--so-impacto", action="store_true", help="só impacto_cold e impacto_sensibilidade (rápido)")
    parser.add_argument("--baterias", nargs="+", help="usa só estas baterias da entrada")
    args = parser.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)

    df = pd.read_parquet(args.entrada)
    if args.baterias:
        df = df[df["bateria"].astype(str).isin(args.baterias)]
    df["nivel"] = pd.Categorical(df["nivel"], categories=NIVEIS, ordered=True)
    for coluna, valor in {"regiao": "us-central1", "pausa": 30}.items():  # parquet do carregador antigo
        if coluna not in df:
            df[coluna] = valor
    login = falhas_login(df)
    if not args.com_login:
        df = df[~df["operacao"].isin(EXCLUIDAS)]

    # iterações descartadas pelo k6 (sem latência): violam o SLI, ficam fora dos percentis
    medidas = df[df["operacao"] != "descartada"]

    # Com mais de uma pausa (tratamento) ou região (bloco) nos dados, as tabelas separam por elas.
    # A região só entra se --por-regiao; sem isso, as regiões são agregadas como réplicas.
    extra = [c for c in ["pausa"] if c in df and df[c].nunique() > 1]
    if args.por_regiao:
        extra.append("regiao")
    if args.so_impacto:
        impacto_cold(df, por=extra).to_csv(args.saida / "impacto_cold.csv")
        impacto_sensibilidade(df, extra).to_csv(args.saida / "impacto_sensibilidade.csv")
        print(f"tabelas em {args.saida}/")
        return
    tabelas = {
        "login": login,
        "percentis": percentis(medidas, extra + ["cenario", "nivel", "servico", "cold"]),
        "percentis_ic": percentis_com_ic(medidas, extra + ["cenario", "nivel"]),
        "sli": sli(df, extra + ["cenario", "nivel"]),
        "sli_servico": sli(df, extra + ["cenario", "nivel", "servico"]),
        "sli_bateria": sli(df, list(dict.fromkeys(extra + ["cenario", "nivel", "regiao", "bateria"]))),
        "impacto_cold": impacto_cold(df, por=extra),
        "impacto_sensibilidade": impacto_sensibilidade(df, extra),
        "mann_whitney": comparacoes(medidas, extra + ["nivel", "servico"]),
        "cv_baterias": cv_baterias(df, extra),
    }
    for nome, tabela in tabelas.items():
        tabela.to_csv(args.saida / f"{nome}.csv")
    with pd.option_context("display.width", 160, "display.max_columns", 20):
        print(tabelas["sli"])
        print(tabelas["percentis_ic"].round(1))
        print(tabelas["impacto_cold"].round(2))
    print(f"tabelas em {args.saida}/")


if __name__ == "__main__":
    main()
