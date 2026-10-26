"""針對 Step 1.4 (M1 -> M2 -> M3 -> M4 ⇄ M5 全流程閉環) 之端到端整合測試 (`test_dplan_e2e.py`)。"""

from __future__ import annotations

import json
from pathlib import Path

from etf_agent.collectors import ExtraSignalCard
from etf_agent.dossier import DossierLedger
from etf_agent.graph import run_dplan_pipeline


def test_day1_and_day2_full_closed_loop_pipeline(tmp_path: Path):
    """驗證從 Day 1 全現金建倉到 Day 2 日常調倉與超限減碼，整條 LangGraph 閉環管線 100% 通過八大鐵律並產出 JSON。"""
    # -----------------------------------------------------------------------
    # 1. Day 1 首日建倉 (10 億全現金，0 持股)
    # -----------------------------------------------------------------------
    day1_datapack = {
        "schema_version": "4.3",
        "doc_type": "DataPack",
        "team_id": "Team_32",
        "trade_date": "2026-05-04",
        "as_of_date": None,
        "generated_at": "2026-05-04T06:00:00+08:00",
        "status": "ok",
        "missing": [],
        "account": {"nav_tradable": 1_000_000_000.0, "cash": 1_000_000_000.0},
        "holdings": [],
    }

    ledger = DossierLedger()
    ledger.get_or_create_link("2308", "2059", relation_note="AI 伺服器電源與機櫃滑軌供應鏈")

    state_day1 = run_dplan_pipeline(
        datapack_input=day1_datapack,
        ledger=ledger,
        output_dir=tmp_path,
    )

    report1 = state_day1["report"]
    assert report1.passed is True, f"Day 1 D-Plan 應通過所有檢核，卻失敗：{report1.errors}"
    assert 20 <= report1.metrics["post_trade_holdings_count"] <= 30
    assert 0.0 <= report1.metrics["estimated_cash_ratio"] <= 0.25

    dplan1_file = Path(state_day1["dplan_path"])
    assert dplan1_file.exists()
    dplan1_json = json.loads(dplan1_file.read_text(encoding="utf-8"))
    assert dplan1_json["doc_type"] == "D-Plan"
    assert len(dplan1_json["orders"]) == report1.metrics["post_trade_holdings_count"]

    # -----------------------------------------------------------------------
    # 2. Day 2 日常調倉 (延續 Day 1 成交後持倉 + 模擬某檔自然漲超 10% 觸發風控減碼)
    # -----------------------------------------------------------------------
    day2_holdings = []
    orders_day1 = dplan1_json["orders"]
    for idx, ord_item in enumerate(orders_day1):
        code = ord_item["ticker"]
        shares = ord_item["shares"]
        price = 1000.0 if code == "2330" else 100.0
        # 故意讓第 2 檔非 2330 持股權重標記為 0.108 (>10% 上限)，測試 M4 自動風控 TRIM
        weight = (shares * price) / 1_000_000_000.0
        if idx == 1 and code != "2330":
            weight = 0.108
        day2_holdings.append(
            {
                "code": code,
                "shares": shares,
                "close_price": price,
                "tradable_weight": round(weight, 6),
            }
        )

    day2_datapack = {
        "schema_version": "4.3",
        "doc_type": "DataPack",
        "team_id": "Team_32",
        "trade_date": "2026-05-05",
        "as_of_date": "2026-05-04",
        "generated_at": "2026-05-05T06:00:00+08:00",
        "status": "ok",
        "missing": [],
        "account": {
            "nav_tradable": 1_000_000_000.0,
            "cash": report1.metrics["estimated_cash"],
        },
        "holdings": day2_holdings,
    }

    sig = ExtraSignalCard(
        ticker="2308",
        authority="mops",
        url="https://mops.twse.com.tw/mops/web/t05st10_ifrs",
        content_as_of="2026-05-04T18:00:00+08:00",
        topic="monthly_revenue",
        statement="2308 台達電公布最新月營收年增 31.2%，AI 伺服器電源出貨暢旺。",
        values={"rev_yoy_pct": 31.2},
        polarity="positive",
        linked_target_ticker="2059",
    )

    state_day2 = run_dplan_pipeline(
        datapack_input=day2_datapack,
        extra_signals=[sig],
        ledger=ledger,
        output_dir=tmp_path,
    )

    report2 = state_day2["report"]
    assert report2.passed is True, f"Day 2 D-Plan 應通過所有檢核，卻失敗：{report2.errors}"
    assert 20 <= report2.metrics["post_trade_holdings_count"] <= 30

    dplan2_file = Path(state_day2["dplan_path"])
    assert dplan2_file.exists()
    dplan2_json = json.loads(dplan2_file.read_text(encoding="utf-8"))
    # 驗證昨日所有持倉 100% 被 decisions 與 no_trade_decisions 完整覆蓋
    covered = {d["ticker"] for d in dplan2_json["decisions"] if d["action"] != "BUY"} | {
        nt["ticker"] for nt in dplan2_json["no_trade_decisions"]
    }
    assert covered == {h["code"] for h in day2_holdings}
