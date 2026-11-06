import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custo import Precos, custo_fixo, custo_variavel, modelo, volume_limite  # noqa: E402
from carregar import descartadas  # noqa: E402
from latencia import boa, delta_cliff, impacto_cold, impacto_sensibilidade, sli  # noqa: E402


def test_erros_e_timeouts_violam_o_slo():
    df = pd.DataFrame({"latencia_ms": [100, 600, 100, 100], "status": [200, 200, 500, 0]})
    assert boa(df).tolist() == [True, False, False, False]


def test_sli_conta_frias_e_percentual():
    df = pd.DataFrame({
        "cenario": ["a"] * 4, "nivel": ["baixo"] * 4,
        "latencia_ms": [900, 100, 100, 100], "status": [200] * 4, "cold": ["1", "0", "0", "0"],
    })
    linha = sli(df, ["cenario", "nivel"]).iloc[0]
    assert (linha["frias"], linha["sli"], linha["cumpre_slo"]) == (1, 75.0, False)


def test_delta_cliff_bate_com_a_definicao():
    rng = np.random.default_rng(0)
    a, b = rng.normal(1, 1, 200), rng.normal(0, 1, 150)
    u = stats.mannwhitneyu(a, b).statistic
    definicao = (np.sign(a[:, None] - b[None, :])).mean()
    assert np.isclose(delta_cliff(u, len(a), len(b)), definicao)


def test_volume_limite_interpola_em_log():
    n = np.array([1e4, 1e6])
    assert np.isclose(volume_limite(n, np.array([98.0, 100.0])), 1e5)
    assert volume_limite(n, np.array([99.5, 100.0])) == 1e4
    assert volume_limite(n, np.array([90.0, 95.0])) is None


def test_custo_fixo_so_no_cenario_b():
    p = Precos()
    assert custo_fixo("a", p) == custo_fixo("c", p) == 0
    # 2 instâncias × 86.400 s × (0,0000025 + 0,0000025 × 0,5)
    assert np.isclose(custo_fixo("b", p), 0.648)
    assert np.isclose(custo_variavel(0.1, p), 0.4e-6 + 0.1 * (0.000024 + 0.0000025 * 0.5))


def test_recomenda_o_mais_barato_entre_os_que_cumprem():
    sli_ = pd.DataFrame({
        "cenario": ["a", "a", "a", "b", "b", "b"],
        "nivel": ["baixo", "medio", "alto"] * 2,
        "requisicoes": [100] * 6, "frias": [5, 2, 1, 0, 0, 0],
        "sli": [95.0, 98.0, 99.9, 99.9, 99.9, 99.9],
    })
    res = modelo(sli_, {"a": 0.1, "b": 0.1}, Precos(), np.array([1e3, 1e7]))
    assert res["recomendado"].tolist() == ["b", "a"]


def test_iteracoes_descartadas_violam_o_slo():
    bruto = pd.DataFrame({
        "metric_name": ["dropped_iterations"] * 2, "timestamp": ["1000", "2000"],
        "metric_value": ["1.0", "2.0"], "scenario": ["ciclo_02", "ciclo_04"],
        "status": [None, None], "extra_tags": ["cenario=a&bateria=1"] * 2,
    })
    d = descartadas(bruto)
    assert d["nivel"].tolist() == ["medio", "baixo", "baixo"]
    medidas = pd.DataFrame({"cenario": ["a"], "nivel": ["medio"], "latencia_ms": [50.0], "status": [200],
                            "cold": ["0"], "operacao": ["get"]})
    linha = sli(pd.concat([medidas, d[d["nivel"] == "medio"]]), ["cenario", "nivel"]).iloc[0]
    assert (linha["descartadas"], linha["sli"]) == (1, 50.0)


def test_impacto_cold_conta_fila_da_instancia_nova():
    df = pd.DataFrame({
        "cenario": ["a"] * 5, "nivel": ["alto"] * 5, "operacao": ["get"] * 4 + ["descartada"],
        "latencia_ms": [8000, 3000, 40, 900, np.nan], "status": [200, 200, 200, 200, 0],
        "cold": ["1", "0", "0", "0", np.nan], "idade_ms": [1000, 1500, 2000, 300000, np.nan],
    })
    t = impacto_cold(df).iloc[0]
    assert (t["frias_marcadas"], t["afetadas"], t["violacoes"], t["violacoes_afetadas"]) == (1, 3, 3, 2)
    assert t["violacoes_frias_marcadas"] == 1 and t["sli_nao_afetadas"] == 0.0


def test_impacto_cold_usa_idade_na_chegada_e_separa_sem_instancia():
    df = pd.DataFrame({
        "cenario": ["a"] * 3, "nivel": ["alto"] * 3, "operacao": ["get"] * 3,
        # esperou a partida lenta; quente de verdade; erro sem instância
        "latencia_ms": [20000, 900, 30000], "status": [200, 200, 500],
        "cold": ["0", "0", "desconhecido"], "idade_ms": [13000, 300000, np.nan],
    })
    t = impacto_cold(df).iloc[0]
    assert (t["violacoes"], t["violacoes_afetadas"], t["violacoes_sem_instancia"]) == (3, 1, 1)
    assert t["violacoes_afetadas_resposta"] == 0 and t["sli_nao_afetadas"] == 0.0
    s = impacto_sensibilidade(df).iloc[0]
    assert s["pct_chegada_10s"] == pytest.approx(100 / 3) and s["pct_resposta_10s"] == 0
    assert s["pct_sem_instancia"] == pytest.approx(100 / 3)


def test_le_metadata_e_tags_da_campanha_multi_regiao(tmp_path):
    from carregar import carrega

    d = tmp_path / "b4"
    d.mkdir()
    pd.DataFrame({
        "metric_name": ["latencia", "dropped_iterations"],
        "timestamp": ["1000", "2000"], "metric_value": ["50.0", "1.0"],
        "scenario": ["ciclo_03", "ciclo_03"], "status": ["200", None],
        "extra_tags": ["regiao=us-east1&pausa=15&cenario=a&bateria=4&ciclo=3&nivel=alto&servico=auth&operacao=get&cold=0",
                       "regiao=us-east1&pausa=15&cenario=a&bateria=4"],
        "metadata": ["idade_ms=4567&instancia=abc", None],
    }).to_csv(d / "a.csv.gz", index=False)
    df = carrega([d])
    medida = df[df["operacao"] == "get"].iloc[0]
    assert (medida["regiao"], medida["pausa"], medida["idade_ms"], medida["instancia"]) == ("us-east1", 15, 4567, "abc")
    descarte = df[df["operacao"] == "descartada"].iloc[0]
    assert (descarte["regiao"], descarte["pausa"], descarte["nivel"]) == ("us-east1", 15, "alto")
