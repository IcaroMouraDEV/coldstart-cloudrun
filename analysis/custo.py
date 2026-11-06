"""Modelo de custo.

    C_i(N) = F_i + v_i · N
    v_i    = p_req + t̄_i · (p_cpu · c + p_mem · m)
    F_A = F_C = 0
    F_B    = n_min · T_ocioso · (p_cpu_ocioso · c + p_mem · m)

n_min = 2: o cenário B tem uma instância mínima em cada serviço (auth-b e backend-b).
t̄_i (tempo médio de instância faturado por requisição) vem do faturamento exportado
para o BigQuery; antes de ele chegar, pode ser passado à mão com --t-medio.

Uso:
    python analysis/custo.py results/tabelas/sli.csv \
        --faturamento PROJETO.faturamento.gcp_billing_export_resource_v1_XXXX \
        --inicio 2026-11-02 --fim 2026-11-06 --requisicoes a=302400,b=302400,c=302400
    python analysis/custo.py results/tabelas/sli.csv --t-medio a=0.15,b=0.12,c=0.16
"""

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from latencia import SLO, TAXAS


@dataclass(frozen=True)
class Precos:
    """Tabela pública us-central1, cobrança por requisição, sem franquia gratuita."""
    p_req: float = 0.40 / 1_000_000   # US$ por requisição
    p_cpu: float = 0.000024           # US$ por vCPU-s ativo
    p_cpu_ocioso: float = 0.0000025   # US$ por vCPU-s de instância mínima ociosa
    p_mem: float = 0.0000025          # US$ por GiB-s (ativo ou ocioso)
    c: float = 1.0                    # vCPU por instância
    m: float = 0.5                    # GiB por instância
    n_min: int = 2                    # instâncias mínimas no cenário B
    t_ocioso: float = 86_400.0        # s por dia de instância mínima ociosa (≤ 86.400)


CONSULTA = """
SELECT
  (SELECT value FROM UNNEST(labels) WHERE key = 'cenario') AS cenario,
  (SELECT value FROM UNNEST(labels) WHERE key = 'servico') AS servico,
  service.description AS produto,
  sku.description AS sku,
  SUM(cost) AS custo,
  SUM(usage.amount) AS uso,
  ANY_VALUE(usage.unit) AS unidade
FROM `{tabela}`
WHERE usage_start_time >= @inicio AND usage_start_time < @fim
  AND EXISTS (SELECT 1 FROM UNNEST(labels) WHERE key = 'experimento' AND value = 'coldstart')
GROUP BY cenario, servico, produto, sku
ORDER BY cenario, servico, sku
"""


def consulta_faturamento(tabela: str, inicio: str, fim: str) -> pd.DataFrame:
    from google.cloud import bigquery

    cliente = bigquery.Client()
    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("inicio", "TIMESTAMP", f"{inicio}T00:00:00Z"),
        bigquery.ScalarQueryParameter("fim", "TIMESTAMP", f"{fim}T00:00:00Z"),
    ])
    return cliente.query(CONSULTA.format(tabela=tabela), job_config=config).to_dataframe()


def t_medio_do_faturamento(fat: pd.DataFrame, requisicoes: dict[str, int], precos: Precos) -> dict[str, float]:
    """t̄_i = vCPU-s ativos faturados no Cloud Run / c / requisições do cenário."""
    run = fat[fat["produto"] == "Cloud Run"]
    sku = run["sku"].str.lower()
    ativo = run[sku.str.contains("cpu allocation time") & ~sku.str.contains("idle")]
    vcpu_s = ativo.groupby("cenario")["uso"].sum()
    return {c: vcpu_s.get(c, 0.0) / precos.c / n for c, n in requisicoes.items()}


def custo_variavel(t_medio: float, p: Precos) -> float:
    return p.p_req + t_medio * (p.p_cpu * p.c + p.p_mem * p.m)


def custo_fixo(cenario: str, p: Precos) -> float:
    if cenario != "b":
        return 0.0
    return p.n_min * p.t_ocioso * (p.p_cpu_ocioso * p.c + p.p_mem * p.m)


def interpola_log(n: np.ndarray, pontos_n: np.ndarray, pontos_y: np.ndarray) -> np.ndarray:
    """Interpolação linear em log10(N); fora da faixa medida, mantém o valor da ponta."""
    ordem = np.argsort(pontos_n)
    return np.interp(np.log10(n), np.log10(pontos_n[ordem]), pontos_y[ordem])


def volume_limite(pontos_n: np.ndarray, pontos_sli: np.ndarray) -> float | None:
    """N_s = min{N : SLI_A(N) ≥ 99%}, por interpolação entre os níveis medidos.

    Devolve None se o cenário A não atinge o SLO em nenhum nível medido, e o menor
    volume medido se já o atinge no nível mais baixo.
    """
    ordem = np.argsort(pontos_n)
    x, y = np.log10(pontos_n[ordem]), pontos_sli[ordem]
    if y[0] >= SLO:
        return float(10 ** x[0])
    for i in range(1, len(x)):
        if y[i] >= SLO:
            frac = (SLO - y[i - 1]) / (y[i] - y[i - 1])
            return float(10 ** (x[i - 1] + frac * (x[i] - x[i - 1])))
    return None


def modelo(sli: pd.DataFrame, t_medio: dict[str, float], p: Precos, grade: np.ndarray) -> pd.DataFrame:
    """Curvas C_i(N), ΔC, κ(N), SLI interpolado e cenário recomendado em cada volume N."""
    sli = sli.assign(volume=sli["nivel"].map(TAXAS) * 86_400)
    res = pd.DataFrame({"volume_diario": grade})
    for c, g in sli.groupby("cenario"):
        res[f"custo_{c}"] = custo_fixo(c, p) + custo_variavel(t_medio[c], p) * grade
        res[f"sli_{c}"] = interpola_log(grade, g["volume"].to_numpy(), g["sli"].to_numpy())

    a = sli[sli["cenario"] == "a"]
    fracao_fria_a = interpola_log(grade, a["volume"].to_numpy(), (a["frias"] / a["requisicoes"]).to_numpy())
    res["cold_starts_dia_a"] = fracao_fria_a * grade
    for c in ("b", "c"):
        if f"custo_{c}" in res:
            res[f"delta_{c}"] = res[f"custo_{c}"] - res["custo_a"]
    if "delta_b" in res:
        res["kappa"] = res["delta_b"] / res["cold_starts_dia_a"].where(res["cold_starts_dia_a"] > 0)

    cenarios = sorted(sli["cenario"].unique())
    custos = res[[f"custo_{c}" for c in cenarios]].to_numpy()
    cumpre = res[[f"sli_{c}" for c in cenarios]].to_numpy() >= SLO
    custos_validos = np.where(cumpre, custos, np.inf)
    melhor = np.argmin(custos_validos, axis=1)
    res["recomendado"] = [cenarios[i] if cumpre[k, i] else "nenhum" for k, i in enumerate(melhor)]
    return res


def _dicionario(texto: str, tipo=float) -> dict:
    return {k: tipo(v) for k, v in (par.split("=") for par in texto.split(","))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sli", type=Path, help="results/tabelas/sli.csv gerado por latencia.py")
    parser.add_argument("--faturamento", help="tabela da exportação do faturamento no BigQuery")
    parser.add_argument("--inicio", help="AAAA-MM-DD, inclusivo")
    parser.add_argument("--fim", help="AAAA-MM-DD, exclusivo")
    parser.add_argument("--requisicoes", help="a=N,b=N,c=N no período; padrão: soma de sli.csv")
    parser.add_argument("--t-medio", help="a=s,b=s,c=s; dispensa o faturamento")
    parser.add_argument("--t-ocioso", type=float, default=Precos.t_ocioso)
    parser.add_argument("-o", "--saida", type=Path, default=Path("results/custo"))
    args = parser.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)

    sli = pd.read_csv(args.sli)
    precos = Precos(t_ocioso=args.t_ocioso)
    requisicoes = (_dicionario(args.requisicoes, int) if args.requisicoes
                   else sli.groupby("cenario")["requisicoes"].sum().to_dict())

    if args.t_medio:
        t_medio = _dicionario(args.t_medio)
    elif args.faturamento:
        fat = consulta_faturamento(args.faturamento, args.inicio, args.fim)
        fat.to_csv(args.saida / "faturamento.csv", index=False)
        print(fat.groupby(["cenario", "produto"])["custo"].sum())
        t_medio = t_medio_do_faturamento(fat, requisicoes, precos)
    else:
        raise SystemExit("informe --faturamento (com --inicio/--fim) ou --t-medio")

    grade = np.logspace(3, 7, 400)
    res = modelo(sli, t_medio, precos, grade)
    res.to_csv(args.saida / "modelo.csv", index=False)

    a = sli[sli["cenario"] == "a"]
    n_s = volume_limite((a["nivel"].map(TAXAS) * 86_400).to_numpy(), a["sli"].to_numpy())
    parametros = {
        "precos": asdict(precos),
        "t_medio_s": t_medio,
        "v_i": {c: custo_variavel(t, precos) for c, t in t_medio.items()},
        "F_i": {c: custo_fixo(c, precos) for c in t_medio},
        "N_s": n_s,
    }
    (args.saida / "parametros.json").write_text(json.dumps(parametros, indent=2, ensure_ascii=False))
    print(json.dumps(parametros, indent=2, ensure_ascii=False))
    faixas = res.groupby((res["recomendado"] != res["recomendado"].shift()).cumsum()).agg(
        de=("volume_diario", "min"), ate=("volume_diario", "max"), cenario=("recomendado", "first"))
    print("Recomendação por faixa de volume diário:\n", faixas.round(0).to_string(index=False))


if __name__ == "__main__":
    main()
