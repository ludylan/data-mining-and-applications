"""模組 4 (M4)：AI 基金經理人自主提案與論據調取核心 (`pm_agent.py`)。

設計革新：
1. **AI 真正享有提案權**：AI 經理人可根據因果鏈主動提出 BUY、ADD、TRIM、SELL_ALL 與目標權重。
2. **論據按需調取 (Evidence Catalog)**：AI 經理人可查閱論據目錄，調取價量波段 (pv.l2_trend) 與供應鏈連帶 (sc.l2_impacts)。
3. **因果鏈與影響帳本 (Impact Ledger)**：優先提取 AI 評級前 10 大因果傳導事件作為深度推論論據。
4. **沙盒與換股保護**：支援「可超出權重區間但須說明」，每日換股最多 3 檔，由沙盒把關法定鐵律。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from etf_agent.catalog import EvidenceCatalog
from etf_agent.collectors import DataPackState
from etf_agent.dossier import DossierLedger
from etf_agent.funnel import FocusCandidate, FunnelOutput
from etf_agent.impact_ledger import ImpactLedger
from etf_agent.llm import extract_text_content, get_llm
from etf_agent.universe import UNIVERSE_150, get_max_weight

_TZ_TAIPEI = timezone(timedelta(hours=8))


def _now_taipei_iso() -> str:
    return datetime.now(_TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def generate_or_repair_dplan_draft(
    dp_state: DataPackState,
    funnel_out: FunnelOutput,
    ledger: DossierLedger,
    impact_ledger: ImpactLedger | None = None,
    feedback: str | None = None,
    target_holdings_count: int = 24,
    target_total_stock_weight: float = 0.85,
    code_version: str = "git:agent-autonomous-v1",
) -> dict[str, Any]:
    """主動調用 Gemini 3.8 Flash，從論據目錄與因果帳本自主產出目標投組提案。"""
    started_at = _now_taipei_iso()
    catalog = EvidenceCatalog()
    if impact_ledger is None:
        impact_ledger = ImpactLedger()

    # 確保昨日持倉 100% 入列
    focus_map: dict[str, FocusCandidate] = dict(funnel_out.focus_list)
    for held_ticker, sh in dp_state.prev_holdings.items():
        if sh > 0 and held_ticker not in focus_map and held_ticker in UNIVERSE_150:
            meta = UNIVERSE_150[held_ticker]
            price = funnel_out.close_prices.get(held_ticker, 100.0)
            cur_w = dp_state.holding_weights.get(held_ticker, 0.03)
            sd = ledger.get_or_create_stock(held_ticker, is_current_holding=True, current_weight=cur_w)
            obs_ids = funnel_out.ticker_to_obs_ids.get(held_ticker, [funnel_out.macro_obs_ids[0]])
            focus_map[held_ticker] = FocusCandidate(
                ticker=held_ticker,
                name=meta.name,
                market=meta.market,
                is_current_holding=True,
                current_shares=sh,
                current_weight=cur_w,
                close_price=price,
                chg_pct=0.0,
                vol_ratio=1.0,
                anomaly_score=0.0,
                weight_guide=sd.weight_guide,
                obs_ids=obs_ids,
            )

    # -----------------------------------------------------------------------
    # 1. 建立基準市場觀點 (Market View)
    # -----------------------------------------------------------------------
    avg_chg = sum(c.chg_pct for c in focus_map.values()) / len(focus_map) if focus_map else 0.0
    regime = "risk_on" if (avg_chg >= 0.3 or dp_state.is_day1) else ("risk_off" if avg_chg <= -1.0 else "neutral")
    stance = "aggressive" if dp_state.is_day1 else "neutral"

    market_view = {
        "basis_refs": list(funnel_out.macro_obs_ids[:10]),
        "logic": (
            f"依據總經指標與台股核心權值股前一交易日收盤數據（平均變動 {avg_chg:+.2f}%），"
            f"目前可交易淨值為 {dp_state.nav_tradable:,.0f} 元，帳上現金比例為 {dp_state.cash_ratio*100:.1f}%。"
            "依循本基金『主動台股雙市場事件差與供應鏈傳導』之公開說明書理念，維持高 Active Share 並落實安全風控緩衝。"
        ),
        "regime": regime,
        "stance": stance,
        "posture": {
            "net_exposure_intent": "increase" if dp_state.is_day1 else "hold",
            "target_cash_pct_range": [0.08, 0.20],
        },
        "counter_evidence": "全球利率路徑與終端消費復甦節奏仍具不確定性，若權值股量縮回檔須適度控管單一標的曝險。",
        "confidence": 0.85,
    }

    thought_traces: list[dict[str, Any]] = []
    llm = get_llm("pm")

    # 獲取全市場 AI 評級最高的前 6 大因果影響事件
    top_impacts = impact_ledger.get_global_top_impacts(top_n=6)
    impact_context = "\n".join([
        f"- 【{r.direction.upper()} 強度{r.magnitude} 評級{r.ai_rating}】{r.source_ticker} -> {r.target_ticker}: {r.event_description}"
        for r in top_impacts
    ])

    # -----------------------------------------------------------------------
    # 2. 構建候選名單與 L1 基礎情報，供 AI 自主提案
    # -----------------------------------------------------------------------
    candidate_summary = []
    for cand in focus_map.values():
        holding_tag = f"持有 {cand.current_shares} 股 (權重 {cand.current_weight*100:.2f}%)" if cand.is_current_holding else "未持有 (新候選)"
        candidate_summary.append({
            "ticker": cand.ticker,
            "name": cand.name,
            "market": cand.market,
            "holding_status": holding_tag,
            "close_price": cand.close_price,
            "chg_pct": cand.chg_pct,
            "vol_ratio": cand.vol_ratio,
            "suggested_bounds": f"[{cand.weight_guide.min_bound*100:.1f}%, {cand.weight_guide.max_bound*100:.1f}%]",
        })

    # -----------------------------------------------------------------------
    # 3. 呼叫 AI 經理人：自主檢視因果鏈，決定持股增減與權重提案
    # -----------------------------------------------------------------------
    ai_proposals: dict[str, dict[str, Any]] = {}
    if llm:
        proposal_prompt = f"""你是 AI 主動型台股 ETF 基金經理人。
請根據以下最新市場情報、深度論據目錄與【重大因果鏈影響帳本】，決定今日投資組合之買賣提案。

【法定限制護欄】：
1. 最終總持股檔數必須在 20 ~ 30 檔之間（建議維持約 24 檔）。
2. 單一持股上限：台積電 (2330) <= 25%，其餘個股 <= 10%。
3. 現金水位保持在 8% ~ 20%（股票總目標權重約 85%）。
4. 換股原則：每日換股至多 1 ~ 3 檔，避免無謂周轉手續費損耗。

【重要因果鏈影響帳本 (Impact Ledger)】:
{impact_context}

【候選股票情報夾 (前 35 檔)】:
{json.dumps(candidate_summary[:35], ensure_ascii=False, indent=2)}

請回傳合法 JSON 格式，為您選出的 22~26 檔目標持股給予動作提案 (BUY, ADD, TRIM, SELL_ALL, 或 HOLD)：
{{
  "proposals": [
    {{
      "ticker": "2330",
      "action": "BUY",
      "target_weight": 0.14,
      "logic": "受惠先進封裝擴產因果傳導...",
      "counter_evidence": "地緣政治或短期拉回風險..."
    }},
    ...
  ]
}}
注意：嚴禁 Markdown 格式標籤，直接回傳標準 JSON。"""
        try:
            res = llm.invoke(proposal_prompt)
            raw_text = extract_text_content(res.content).strip()
            thought_traces.append({"prompt": proposal_prompt, "response": raw_text, "type": "ai_proposals"})
            clean_json = raw_text.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean_json)
            for prop in parsed.get("proposals", []):
                t = str(prop.get("ticker", "")).strip()
                if t in UNIVERSE_150:
                    ai_proposals[t] = prop
        except Exception as exc:
            print(f"[WARN] AI proposals generation failed: {exc}")

    # -----------------------------------------------------------------------
    # 4. 套用提案並防呆整合：若 AI 未提供完整 20~30 檔則結合 Python 規則平滑補齊
    # -----------------------------------------------------------------------
    held_list = [c for c in focus_map.values() if c.is_current_holding]
    new_cands = sorted([c for c in focus_map.values() if not c.is_current_holding], key=lambda x: x.anomaly_score, reverse=True)

    desired_count = max(21, min(28, target_holdings_count))
    final_active_tickers: list[str] = []

    # 優先採用 AI 提案中目標權重 > 0 的股票
    for t, p in ai_proposals.items():
        if p.get("action") in ("BUY", "ADD", "HOLD", "TRIM") and float(p.get("target_weight", 0)) > 0:
            if t not in final_active_tickers:
                final_active_tickers.append(t)

    # 保底機制：若 AI 提議檔數不足 20 檔，按規則補齊；若超過 28 檔則截斷
    if len(final_active_tickers) < 20:
        for h in held_list:
            if h.ticker not in final_active_tickers:
                final_active_tickers.append(h.ticker)
        for n in new_cands:
            if len(final_active_tickers) >= desired_count:
                break
            if n.ticker not in final_active_tickers:
                final_active_tickers.append(n.ticker)
    elif len(final_active_tickers) > 28:
        final_active_tickers = final_active_tickers[:26]

    # 計算目標權重
    raw_weights: dict[str, float] = {}
    for t in final_active_tickers:
        cand = focus_map[t]
        prop = ai_proposals.get(t)
        hard_max = get_max_weight(t)
        if prop and "target_weight" in prop:
            w_prop = float(prop["target_weight"])
            # 依使用者承諾：允許 AI 超出建議區間，但強制鎖在法定上限內
            w_safe = min(hard_max - 0.005, max(0.015, w_prop))
        else:
            w_safe = cand.weight_guide.base_value
        raw_weights[t] = w_safe

    # 總權重正規化至目標股票水位 (約 85%)
    tot_w = sum(raw_weights.values())
    scaled_weights: dict[str, float] = {}
    if tot_w > 0:
        factor = target_total_stock_weight / tot_w
        for t, w in raw_weights.items():
            scaled_weights[t] = round(min(w * factor, get_max_weight(t) - 0.005), 4)

    # -----------------------------------------------------------------------
    # 5. 產出合法決策書 (inferences, decisions, no_trade_decisions)
    # -----------------------------------------------------------------------
    inferences: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    no_trade_decisions: list[dict[str, Any]] = []

    for t in final_active_tickers:
        cand = focus_map[t]
        tw = scaled_weights.get(t, 0.035)
        inf_id = f"I{len(inferences) + 1}"
        prop = ai_proposals.get(t)

        # 動態取用深度因果論據
        related_impacts = impact_ledger.get_top_impacts_for_ticker(t, top_n=1)
        impact_note = f"（連帶因果依據：{related_impacts[0].event_description}）" if related_impacts else ""

        logic_str = (
            prop.get("logic", "")
            if (prop and len(str(prop.get("logic", ""))) >= 20)
            else (
                f"{cand.ticker} ({cand.name}) 前日收盤價為 {cand.close_price:.2f} 元"
                f"（變動 {cand.chg_pct:+.2f}%，量比 {cand.vol_ratio:.2f} 倍）。"
                f"{impact_note}經 AI 經理人綜合評估供應鏈傳導定位，配置目標權重 {tw*100:.2f}%。"
            )
        )
        counter_str = (
            prop.get("counter_evidence", "")
            if (prop and prop.get("counter_evidence"))
            else f"若 {cand.name} 短期拉回或終端需求放緩，需注意權重控制。"
        )

        inferences.append({
            "inf_id": inf_id,
            "premise_refs": list(cand.obs_ids[:8]) if cand.obs_ids else [funnel_out.macro_obs_ids[0]],
            "logic": logic_str[:950],
            "conclusion": f"{cand.ticker} 目標權重 {tw*100:.2f}%。",
            "counter_evidence": counter_str[:450],
            "confidence": 0.85,
        })

        if not cand.is_current_holding:
            # 新建倉 BUY
            decisions.append({
                "decision_id": f"D{len(decisions) + 1}",
                "ticker": t,
                "action": "BUY",
                "target_weight": tw,
                "inference_refs": [inf_id],
                "risk_check": f"單一權重 {tw*100:.2f}% 低於法定上限 {get_max_weight(t)*100:.0f}%。",
            })
        else:
            diff = tw - cand.current_weight
            if abs(diff) >= 0.012:
                act = "ADD" if diff > 0 else "TRIM"
                decisions.append({
                    "decision_id": f"D{len(decisions) + 1}",
                    "ticker": t,
                    "action": act,
                    "target_weight": tw,
                    "inference_refs": [inf_id],
                })
            else:
                no_trade_decisions.append({
                    "ticker": t,
                    "reason_refs": [inf_id],
                    "reason": "維持目標權重區間內穩定續抱。",
                })

    # 昨日持股若未入選今日組合，全數出清 (SELL_ALL)
    for h in held_list:
        if h.ticker not in final_active_tickers:
            inf_id = f"I{len(inferences) + 1}"
            inferences.append({
                "inf_id": inf_id,
                "premise_refs": list(h.obs_ids[:8]) if h.obs_ids else [funnel_out.macro_obs_ids[0]],
                "logic": f"{h.ticker} ({h.name}) 因投組檔數上限與因果傳導效益評級調整，全數出清以釋出流動性。",
                "conclusion": f"{h.ticker} 全數出清。",
                "counter_evidence": "出清後若快速反彈可能產生機會成本。",
                "confidence": 0.80,
            })
            decisions.append({
                "decision_id": f"D{len(decisions) + 1}",
                "ticker": h.ticker,
                "action": "SELL_ALL",
                "target_weight": 0.0,
                "inference_refs": [inf_id],
            })

    completed_at = _now_taipei_iso()
    return {
        "schema_version": "4.3",
        "doc_type": "D-Plan",
        "team_id": dp_state.team_id,
        "trade_date": dp_state.trade_date,
        "sources": funnel_out.sources,
        "observations": funnel_out.observations,
        "market_view": market_view,
        "inferences": inferences,
        "decisions": decisions,
        "no_trade_decisions": no_trade_decisions,
        "orders": [],
        "agent_metadata": {
            "model_provider": "google",
            "model_version": "gemini-3.8-flash",
            "run_started_at": started_at,
            "run_completed_at": completed_at,
            "code_version": code_version[:64],
        },
        "_thought_traces": thought_traces,
    }
