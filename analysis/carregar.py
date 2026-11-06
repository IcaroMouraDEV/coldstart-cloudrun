"""Lê as saídas CSV do k6 e gera uma tabela única com as amostras da Trend `latencia`.

Uso: python analysis/carregar.py results/2026*-b* -o results/latencia.parquet

Cada diretório de bateria (gerado por load/run-bateria.sh) tem um <cenario>.csv.gz.
Lê os dois formatos: até a bateria 3, instancia e idade_ms eram tags (extra_tags) e não
havia regiao nem pausa; a partir da campanha multi-região, instancia e idade_ms vêm na
coluna `metadata`, e as baterias antigas recebem os valores da época (PADROES).
"""

import argparse
from pathlib import Path
from urllib.parse import parse_qsl

import pandas as pd

NIVEIS = ["baixo", "medio", "alto"]  # ordem dos ciclos em load/bateria.js
TAGS = ["regiao", "pausa", "cenario", "bateria", "ciclo", "nivel", "servico", "operacao", "cold",
        "instancia", "idade_ms"]
PADROES = {"regiao": "us-central1", "pausa": "30"}  # baterias 1–3


def _campos(serie: pd.Series, index=None) -> pd.DataFrame:
    return pd.DataFrame([dict(parse_qsl(t, keep_blank_values=True)) for t in serie.fillna("")], index=index)


def le_csv(caminho: Path) -> pd.DataFrame:
    partes = []
    colunas = ["metric_name", "timestamp", "metric_value", "scenario", "status", "extra_tags", "metadata"]
    for pedaco in pd.read_csv(caminho, usecols=lambda c: c in colunas, dtype=str, chunksize=500_000):
        partes.append(pedaco[pedaco["metric_name"].isin(["latencia", "dropped_iterations"])])
    df = pd.concat(partes, ignore_index=True)
    descartes = df[df["metric_name"] == "dropped_iterations"]
    df = df[df["metric_name"] == "latencia"].reset_index(drop=True)

    tags = _campos(df["extra_tags"])
    if "metadata" in df:  # instancia e idade_ms como metadata (campanha multi-região)
        meta = _campos(df["metadata"])
        tags = tags.combine_first(meta.reindex(columns=["instancia", "idade_ms"]))
    tags = tags.reindex(columns=TAGS).fillna(PADROES)
    return pd.DataFrame({
        "instante": pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True),
        "latencia_ms": df["metric_value"].astype(float),
        "status": df["status"].astype(int),
        **{t: tags[t] for t in TAGS},
    }).pipe(lambda d: pd.concat([d, descartadas(descartes)], ignore_index=True))


def descartadas(descartes: pd.DataFrame) -> pd.DataFrame:
    """Iterações que o k6 não disparou por falta de VU livre (picos de cold start).

    Viram linhas sem latência, status 0 e operacao/servico "descartada": contam como
    violação no SLI e ficam fora dos percentis.
    """
    tags = _campos(descartes["extra_tags"], index=descartes.index).reindex(columns=TAGS).fillna(PADROES)
    ciclo = descartes["scenario"].str.removeprefix("ciclo_").astype(int)
    linhas = descartes.index.repeat(descartes["metric_value"].astype(float).round().astype(int))
    return pd.DataFrame({
        "instante": pd.to_datetime(descartes["timestamp"].astype("int64"), unit="ms", utc=True),
        "latencia_ms": float("nan"),
        "status": 0,
        "regiao": tags["regiao"],
        "pausa": tags["pausa"],
        "cenario": tags.get("cenario"),
        "bateria": tags.get("bateria"),
        "ciclo": ciclo.astype(str),
        "nivel": ciclo.map(lambda c: NIVEIS[(c - 1) % len(NIVEIS)]),
        "servico": "descartada",
        "operacao": "descartada",
    }).loc[linhas].reset_index(drop=True)


def carrega(diretorios: list[Path]) -> pd.DataFrame:
    arquivos = sorted(a for d in diretorios for a in Path(d).glob("*.csv.gz"))
    if not arquivos:
        raise SystemExit("nenhum *.csv.gz encontrado")
    df = pd.concat([le_csv(a) for a in arquivos], ignore_index=True)
    df["bateria"] = df["bateria"].astype(int)
    df["ciclo"] = df["ciclo"].astype(int)
    df["pausa"] = df["pausa"].astype(int)
    # idade da instância na chegada (X-Instance-Age-Ms); vazia em erros e timeouts
    df["idade_ms"] = pd.to_numeric(df["idade_ms"], errors="coerce")
    for c in ["regiao", "cenario", "nivel", "servico", "operacao", "cold"]:
        df[c] = df[c].astype("category")
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("diretorios", nargs="+", type=Path)
    parser.add_argument("-o", "--saida", type=Path, default=Path("results/latencia.parquet"))
    args = parser.parse_args()

    df = carrega(args.diretorios)
    df.to_parquet(args.saida, index=False)
    print(f"{len(df)} requisições → {args.saida}")
    print(df.groupby(["cenario", "bateria"], observed=True).size().unstack())


if __name__ == "__main__":
    main()
