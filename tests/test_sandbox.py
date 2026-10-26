"""針對 M5 沙盒試算與合規守門員 (`src/etf_agent/sandbox.py`) 之完整單元測試。"""

from __future__ import annotations

import copy

from etf_agent.sandbox import (
    align_posture_and_funding,
    compute_target_shares,
    derive_orders_and_normalize,
    verify_dplan,
)
from etf_agent.universe import UNIVERSE_150


def _build_valid_fixture():
    """建立一組完全符合官方 v4.3 規範的 22 檔持倉基準情境 (供正向與負向測試複用)。"""
    tickers = list(UNIVERSE_150.keys())[:22]  # 2330 + 前 21 檔上市白名單
    nav_tradable = 1_000_000_000.0  # 10 億
    close_prices = {t: 100.0 for t in tickers}
    close_prices["2330"] = 1000.0  # 台積電 1000 元

    # 昨日已持有前 20 檔：
    # 2330 持有 150,000 股 (市值 1.5 億 = 15%)
    # 其餘 19 檔各持有 350,000 股 (每檔市值 3,500 萬 = 3.5%，19 檔共 66.5%)
    # 昨日總持股市值 = 81.5% (8.15 億)，昨日現金 = 1.85 億 (18.5%)
    prev_holdings: dict[str, int] = {"2330": 150_000}
    for t in tickers[1:20]:
        prev_holdings[t] = 350_000
    prev_cash = 185_000_000.0

    # 今日決策：
    # D1: ADD 2330 目標權重 0.16 (160,000 股 -> 買 10,000 股 = 1,000 萬)
    # D2: TRIM tickers[1] (2454) 目標權重 0.03 (300,000 股 -> 賣 50,000 股 = 500 萬)
    # D3: BUY tickers[20] 目標權重 0.035 (350,000 股 -> 買 350,000 股 = 3,500 萬)
    # D4: BUY tickers[21] 目標權重 0.035 (350,000 股 -> 買 350,000 股 = 3,500 萬)
    # 其餘 tickers[2..19] 共 18 檔放入 no_trade_decisions
    dplan = {
        "schema_version": "4.3",
        "doc_type": "D-Plan",
        "team_id": "Team_32",
        "trade_date": "2026-05-04",
        "sources": [
            {
                "source_id": "S1",
                "authority": "twse",
                "url": "https://www.twse.com.tw/zh/trading/historical/mi-index.html",
                "name": "TWSE 每日收盤行情",
                "content_as_of": "2026-05-03T14:30:00+08:00",
            }
        ],
        "observations": [
            {
                "obs_id": "O1",
                "source_ref": ["S1"],
                "topic": "price_volume",
                "statement": "台積電與主要權值股昨日收盤價量表現穩健，成交量溫和放大。",
                "values": {"close_2330": 1000.0, "volume_ratio": 1.25},
            }
        ],
        "market_view": {
            "basis_refs": ["O1"],
            "logic": "台股權值股價量結構維持多頭排列，電子上下游營收動能穩健，採取溫和加碼配置並維持適度現金緩衝。",
            "regime": "risk_on",
            "stance": "aggressive",
            "posture": {
                "net_exposure_intent": "increase",
                "target_cash_pct_range": [0.09, 0.14],
            },
            "counter_evidence": "美債殖利率短線小幅反彈，須留意高本益比股震盪。",
            "confidence": 0.8,
        },
        "inferences": [
            {
                "inf_id": "I1",
                "premise_refs": ["O1"],
                "logic": "先進製程與伺服器供應鏈需求強勁，支撐核心權值股與衛星標的維持既定權重區間。",
                "conclusion": "加碼核心權值並新建衛星部位，其餘穩健續抱。",
                "counter_evidence": null_or_str(None),
                "confidence": 0.8,
            }
        ],
        "decisions": [
            {
                "decision_id": "D1",
                "ticker": "2330",
                "action": "ADD",
                "target_weight": 0.16,
                "inference_refs": ["I1"],
            },
            {
                "decision_id": "D2",
                "ticker": tickers[1],
                "action": "TRIM",
                "target_weight": 0.03,
                "inference_refs": ["I1"],
            },
            {
                "decision_id": "D3",
                "ticker": tickers[20],
                "action": "BUY",
                "target_weight": 0.035,
                "inference_refs": ["I1"],
            },
            {
                "decision_id": "D4",
                "ticker": tickers[21],
                "action": "BUY",
                "target_weight": 0.035,
                "inference_refs": ["I1"],
            },
        ],
        "no_trade_decisions": [
            {
                "ticker": t,
                "reason_refs": ["I1"],
                "reason": "基本面與價量趨勢符合預期，維持既有部位續抱。",
            }
            for t in tickers[2:20]
        ],
        "orders": [],
        "agent_metadata": {
            "model_provider": "google",
            "model_version": "gemini-2.5-pro",
            "run_started_at": "2026-05-04T06:10:00+08:00",
            "run_completed_at": "2026-05-04T06:12:30+08:00",
            "code_version": "git:step1.2-sandbox",
        },
    }

    dplan = derive_orders_and_normalize(dplan, nav_tradable, prev_holdings, close_prices)
    return dplan, nav_tradable, prev_cash, prev_holdings, close_prices, tickers


def null_or_str(val):
    return val


def test_compute_target_shares_official_formula():
    """測試官方 §⑥ 千股無條件捨去公式。"""
    # floor(0.0355 * 1,000,000,000 / (123.5 * 1000)) * 1000
    # = floor(35,500,000 / 123,500) * 1000 = floor(287.449) * 1000 = 287,000
    shares = compute_target_shares(0.0355, 1_000_000_000, 123.5, "BUY")
    assert shares == 287_000
    assert shares % 1000 == 0
    assert compute_target_shares(0.05, 1_000_000_000, 100.0, "SELL_ALL") == 0


def test_valid_dplan_passes_all_8_checks():
    """測試完全合規的 22 檔 D-Plan 順利通過全部 8 道檢核。"""
    dplan, nav, prev_cash, prev_holdings, prices, _ = _build_valid_fixture()
    report = verify_dplan(dplan, nav, prev_cash, prev_holdings, prices)
    assert report.passed is True, f"預期通過卻失敗：{report.errors}"
    assert report.metrics["post_trade_holdings_count"] == 22
    assert 0.09 <= report.metrics["estimated_cash_ratio"] <= 0.14


def test_zero_lot_decision_auto_moves_to_no_trade_and_renumbers():
    """測試子模組 5.1：當異動不足 1 張 (0 股) 時，自動移回 no_trade_decisions 並重編 D1..Dk。"""
    dplan, nav, prev_cash, prev_holdings, prices, tickers = _build_valid_fixture()

    # 將 D2 (tickers[1], 原本持有 350,000 股 = 3.5%) 的 target_weight 設為 0.03505
    # 因為股價 100 元，floor(0.03505 * 1e9 / 100,000) * 1000 = 350,000 股 -> delta = 0 股！
    dplan["decisions"][1]["target_weight"] = 0.03505
    normalized = derive_orders_and_normalize(dplan, nav, prev_holdings, prices)

    # 原本 4 筆 decisions 應該縮減為 3 筆 (D1, D2, D3)，且無跳號
    dec_ids = [d["decision_id"] for d in normalized["decisions"]]
    assert dec_ids == ["D1", "D2", "D3"]
    assert len(normalized["orders"]) == 3

    # 原本的 tickers[1] 應該自動出現在 no_trade_decisions 中，維持 100% 昨日持倉覆蓋率
    nt_tickers = {nt["ticker"] for nt in normalized["no_trade_decisions"]}
    assert tickers[1] in nt_tickers

    report = verify_dplan(normalized, nav, prev_cash, prev_holdings, prices)
    assert report.passed is True, f"0 股自動移轉後應 100% 合規：{report.errors}"


def test_check_2_detects_id_gap_and_dangling_ref():
    """測試 Check 2：捕捉跳號 (如 O1, O3) 與懸空引用 (如 I99)。"""
    dplan, nav, prev_cash, prev_holdings, prices, _ = _build_valid_fixture()
    bad = copy.deepcopy(dplan)
    bad["observations"][0]["obs_id"] = "O2"  # 跳過 O1
    bad["decisions"][0]["inference_refs"] = ["I99"]  # 不存在的推論

    report = verify_dplan(bad, nav, prev_cash, prev_holdings, prices)
    assert report.passed is False
    err_text = "\n".join(report.errors)
    assert "[Check 2 引用鏈]" in err_text
    assert "O2" in err_text
    assert "I99" in err_text


def test_check_3_detects_missing_yesterday_holding():
    """測試 Check 3：捕捉遺漏昨日持倉未覆蓋。"""
    dplan, nav, prev_cash, prev_holdings, prices, _ = _build_valid_fixture()
    bad = copy.deepcopy(dplan)
    # 故意刪掉最後一筆 no_trade_decision，導致少覆蓋 1 檔昨日持股
    dropped_ticker = bad["no_trade_decisions"].pop()["ticker"]

    report = verify_dplan(bad, nav, prev_cash, prev_holdings, prices)
    assert report.passed is False
    err_text = "\n".join(report.errors)
    assert "[Check 3 持倉覆蓋]" in err_text
    assert dropped_ticker in err_text


def test_check_4_and_5_detect_holdings_count_and_weight_cap():
    """測試 Check 4 & 5：捕捉非白名單股票、持股檔數不足 20 檔、以及權重超標 (>10%)。"""
    dplan, nav, prev_cash, prev_holdings, prices, tickers = _build_valid_fixture()
    bad = copy.deepcopy(dplan)
    # 讓 D3 (非 2330 一般個股) target_weight 設為 0.12 (超過 10% 上限)
    bad["decisions"][2]["target_weight"] = 0.12
    bad = derive_orders_and_normalize(bad, nav, prev_holdings, prices)

    report = verify_dplan(bad, nav, prev_cash, prev_holdings, prices)
    assert report.passed is False
    assert any("[Check 5 權重上限]" in e for e in report.errors)


def test_check_6_and_7_detect_posture_and_unnecessary_funding_for_and_auto_align():
    """測試 Check 6 & 7 捕捉 posture 矛盾與多餘 funding_for，並驗證子模組 5.3 自動校準修復。"""
    dplan, nav, prev_cash, prev_holdings, prices, _ = _build_valid_fixture()
    bad = copy.deepcopy(dplan)
    # 原本是淨買超 (increase)，故意填成 reduce，且現金區間填錯 [0.20, 0.24]
    bad["market_view"]["posture"]["net_exposure_intent"] = "reduce"
    bad["market_view"]["posture"]["target_cash_pct_range"] = [0.20, 0.24]
    # 在現金充足 (約 11%) 下故意標註 D2 (賣出 500 萬) funding_for D1，觸發「錢夠卻宣稱調度」
    bad["decisions"][1]["funding_for"] = ["D1"]

    report_before = verify_dplan(bad, nav, prev_cash, prev_holdings, prices)
    assert report_before.passed is False
    err_text = "\n".join(report_before.errors)
    assert "[Check 6 現金區間]" in err_text
    assert "[Check 7 Posture]" in err_text

    # 呼叫子模組 5.3 自動對齊器修復
    aligned = align_posture_and_funding(bad, prev_cash, nav, prices)
    report_after = verify_dplan(aligned, nav, prev_cash, prev_holdings, prices)
    assert report_after.passed is True, f"自動對齊後應通過檢核：{report_after.errors}"
    assert aligned["market_view"]["posture"]["net_exposure_intent"] == "increase"
    assert "funding_for" not in aligned["decisions"][1]


def test_check_8_detects_low_active_share():
    """測試 Check 8：當投資組合與某檔主動型 ETF 過度雷同時 (Active Share < 15%) 報錯。"""
    dplan, nav, prev_cash, prev_holdings, prices, _ = _build_valid_fixture()
    report_ok = verify_dplan(dplan, nav, prev_cash, prev_holdings, prices)
    # 構造一個跟我們成交後權重一模一樣的 ETF 基準 -> Active Share = 0.0 < 0.15
    identical_bench = {"00980A": dict(report_ok.metrics["post_trade_weights"])}
    report_fail = verify_dplan(
        dplan, nav, prev_cash, prev_holdings, prices, etf_benchmarks=identical_bench
    )
    assert report_fail.passed is False
    assert any("[Check 8 Active Share]" in e for e in report_fail.errors)
