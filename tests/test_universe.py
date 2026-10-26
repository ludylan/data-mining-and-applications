"""測試 M1 字典 (`universe.py`) 與 M1.5 個股及連結履歷庫 (`dossier.py`)。"""

import pytest

from etf_agent.dossier import DossierLedger, EvidenceEntry
from etf_agent.universe import (
    ACTIVE_ETFS_30,
    UNIVERSE_150,
    compute_active_share,
    get_authority,
    get_max_weight,
    is_valid_ticker,
)


def test_universe_counts_and_markets():
    """確認 150 檔白名單剛好是 100 檔上市 (twse) + 50 檔上櫃 (tpex)，無死板產業分類。"""
    assert len(UNIVERSE_150) == 150
    twse_count = sum(1 for m in UNIVERSE_150.values() if m.market == "twse")
    tpex_count = sum(1 for m in UNIVERSE_150.values() if m.market == "tpex")
    assert twse_count == 100
    assert tpex_count == 50
    assert len(ACTIVE_ETFS_30) == 30


def test_authority_and_max_weights():
    assert is_valid_ticker("2330")
    assert is_valid_ticker("5274")
    assert not is_valid_ticker("0050")
    assert get_authority("2330") == "twse"
    assert get_authority("5274") == "tpex"
    assert get_max_weight("2330") == 0.25
    assert get_max_weight("2317") == 0.10
    with pytest.raises(KeyError):
        get_authority("9999")


def test_dossier_bounded_adjustment_and_evidences(tmp_path):
    """確認 Python 先給出可行區間，AI 根據正反證據在區間內上下浮動並維護連結履歷。"""
    ledger = DossierLedger()

    # 1. 建立台達電 (2308) -> 順達 (3211) 的連結履歷
    link = ledger.get_or_create_link(
        source_ticker="2308",
        target_ticker="3211",
        relation_note="AI 伺服器電源與電池備援模組 (BBU) 供應關係",
        price_corr_20d=0.6,
        has_direct_revenue_tie=True,
    )
    # Python 算出的基準強度為 0.5*0.6 + 0.3 = 0.60, 允許區間 [0.35, 0.85]
    assert link.strength.base_value == 0.60
    assert link.strength.min_bound == 0.35
    assert link.strength.max_bound == 0.85

    # 2. 加入一筆強化連動證據與一筆削弱證據
    link.add_evidence(
        EvidenceEntry(
            date="2026-10-25",
            obs_ref="O4",
            summary="台達電法說指出 GB200 伺服器機櫃 BBU 標配率提升",
            polarity="positive",
            metrics={"bbu_adoption_rate": 0.8},
        )
    )
    link.add_evidence(
        EvidenceEntry(
            date="2026-10-25",
            obs_ref="O9",
            summary="順達消費性筆電電池業務仍佔 40%，受 PC 需求平淡拖累",
            polarity="negative",
            metrics={"nb_rev_share": 0.4},
        )
    )
    assert len(link.supporting_evidences) == 1
    assert len(link.weakening_evidences) == 1

    # 3. AI 根據證據想要把強度調高到 0.99 (超出上限)，會被自動約束在 max_bound (0.85)
    adjusted = link.strength.clamp_and_set(0.99, "因 O4 GB200 BBU 需求爆發調升強度")
    assert adjusted == 0.85

    # 4. 測試訊號傳遞
    impacts = ledger.propagate_signal("2308", source_signal=0.8)
    assert impacts["3211"] == round(0.8 * 0.85, 4)

    # 5. 確認可完整存檔
    save_path = tmp_path / "dossiers.json"
    ledger.save(save_path)
    assert save_path.exists()


def test_active_share_formula():
    w_agent = {"2330": 0.15, "5274": 0.08, "3131": 0.07}
    w_bench = {"2330": 0.25, "2317": 0.10}
    # 0.5 * (|0.15-0.25| + |0.08-0| + |0.07-0| + |0-0.10|) = 0.5 * 0.35 = 0.175
    assert round(compute_active_share(w_agent, w_bench), 4) == 0.175
