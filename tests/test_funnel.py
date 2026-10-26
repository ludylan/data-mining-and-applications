"""針對 Step 1.3 (M2 `collectors.py` 與 M3 `funnel.py`) 之單元測試。"""

from __future__ import annotations

import pytest

from etf_agent.collectors import (
    ExtraSignalCard,
    QuoteRecord,
    SettlementNotReadyError,
    parse_datapack,
)
from etf_agent.dossier import DossierLedger
from etf_agent.funnel import build_funnel_and_observations


def test_parse_datapack_blocks_unsettled_data():
    """測試當官方前日結算未完成 (`prev_settlement` in missing) 時，立即攔截並說明原因。"""
    unsettled_raw = {
        "schema_version": "4.3",
        "doc_type": "DataPack",
        "team_id": "Team_32",
        "trade_date": "2026-05-05",
        "as_of_date": "2026-05-04",
        "status": "error",
        "missing": ["prev_settlement"],
        "account": None,
        "holdings": None,
    }
    with pytest.raises(SettlementNotReadyError) as exc_info:
        parse_datapack(unsettled_raw)
    assert "結算尚未完成" in str(exc_info.value)


def test_day1_and_day2_funnel_retains_holdings_and_numbers_s_and_o():
    """測試 M3 漏斗：
    1. Day 1 自動挑選約 26 檔候選股（含 2330）。
    2. Day 2+ 自動 100% 保留昨日全部持股 + 透過連結履歷傳導挑出新機會股。
    3. 產生的 S1..Sn 與 O1..On 嚴格從 1 連續遞增無跳號，且每檔 Focus List 個股皆有對應 O。
    """
    # 1. Day 1 全現金測試
    day1_raw = {
        "schema_version": "4.3",
        "doc_type": "DataPack",
        "team_id": "Team_32",
        "trade_date": "2026-05-04",
        "as_of_date": None,
        "generated_at": "2026-05-04T06:00:00+08:00",
        "status": "ok",
        "missing": [],
        "account": {"nav_tradable": 1_000_000_000, "cash": 1_000_000_000},
        "holdings": [],
    }
    dp1 = parse_datapack(day1_raw)
    assert dp1.is_day1 is True
    out1 = build_funnel_and_observations(dp1, day1_target_candidates=26)
    assert len(out1.focus_list) == 26
    assert "2330" in out1.focus_list
    assert out1.focus_list["2330"].weight_guide.base_value == 0.15

    # 2. Day 2 既有持倉 + 跨股連結傳導測試
    day2_raw = {
        "schema_version": "4.3",
        "doc_type": "DataPack",
        "team_id": "Team_32",
        "trade_date": "2026-05-05",
        "as_of_date": "2026-05-04",
        "generated_at": "2026-05-05T06:00:00+08:00",
        "status": "ok",
        "missing": [],
        "account": {"nav_tradable": 1_010_000_000, "cash": 150_000_000},
        "holdings": [
            {"code": "2330", "shares": 150_000, "close_price": 1010.0, "tradable_weight": 0.15},
            {"code": "2308", "shares": 200_000, "close_price": 400.0, "tradable_weight": 0.0792},
        ],
    }
    dp2 = parse_datapack(day2_raw)
    assert dp2.is_day1 is False
    assert set(dp2.prev_holdings.keys()) == {"2330", "2308"}

    # 建立連結履歷：2308 (台達電) -> 2059 (川湖)，並塞入一筆正向訊號
    ledger = DossierLedger()
    ledger.get_or_create_link("2308", "2059", relation_note="AI 伺服器電源與滑軌供應鏈協同")
    sig = ExtraSignalCard(
        ticker="2308",
        authority="mops",
        url="https://mops.twse.com.tw/mops/web/t05st10_ifrs",
        content_as_of="2026-05-04T17:30:00+08:00",
        topic="monthly_revenue",
        statement="2308 台達電公布最新月營收創同期新高，AI 伺服器電源需求強勁。",
        values={"rev_yoy_pct": 28.5},
        polarity="positive",
        linked_target_ticker="2059",
    )

    out2 = build_funnel_and_observations(
        dp2,
        extra_signals=[sig],
        ledger=ledger,
        max_new_candidates=5,
    )

    # 昨日持倉 2330, 2308 必須 100% 保留，且受傳導帶動的 2059 也應進入 Focus List
    assert "2330" in out2.focus_list
    assert "2308" in out2.focus_list
    assert "2059" in out2.focus_list

    # 檢查 S1..Sn 與 O1..On 100% 連續不跳號
    s_ids = [s["source_id"] for s in out2.sources]
    o_ids = [o["obs_id"] for o in out2.observations]
    assert s_ids == [f"S{i}" for i in range(1, len(out2.sources) + 1)]
    assert o_ids == [f"O{i}" for i in range(1, len(out2.observations) + 1)]

    # 檢查每檔 Focus List 個股都有至少 1 筆 O ID 可供引用
    for ticker, cand in out2.focus_list.items():
        assert len(cand.obs_ids) >= 1
        for oid in cand.obs_ids:
            assert oid in o_ids
