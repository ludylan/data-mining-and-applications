"""模組 4 (M4)：AI 基金經理決策核心與退回補齊邏輯 (`pm_agent.py`)。

對應上游規格書：`docs/specs/M4_pm_agent_spec.md`。

一句話定位：
接收 M3 整理好的情報夾 (`S1..Sn`, `O1..On`, `Focus List` 與 `Dossier` 權重區間)，
產出大盤觀點 (`market_view`)、個股因果推論 (`I1..In`) 與買賣/續抱決策 (`D1..Dk` / `no_trade_decisions`)；
若收到 M5 沙盒的「退回原因 + 補齊指引」，則針對缺漏重新思考並自動補齊修正。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from etf_agent.collectors import DataPackState
from etf_agent.dossier import DossierLedger
from etf_agent.funnel import FocusCandidate, FunnelOutput
from etf_agent.llm import extract_text_content, get_llm
from etf_agent.universe import UNIVERSE_150, get_max_weight

_TZ_TAIPEI = timezone(timedelta(hours=8))


def _now_taipei_iso() -> str:
    return datetime.now(_TZ_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def generate_or_repair_dplan_draft(
    dp_state: DataPackState,
    funnel_out: FunnelOutput,
    ledger: DossierLedger,
    feedback: str | None = None,
    target_holdings_count: int = 24,
    target_total_stock_weight: float = 0.85,
    code_version: str = "git:sprint1-mvp",
) -> dict[str, Any]:
    """根據 M3 情報夾與 M1.5 連結履歷產出 D-Plan 決策草案；若帶有 `feedback` 則執行針對性補齊修正。"""
    started_at = _now_taipei_iso()

    # -----------------------------------------------------------------------
    # 0. 若有來自 M5 沙盒的「退回原因 + 補齊指引」，先解析並調整參數
    # -----------------------------------------------------------------------
    if feedback:
        if "持股檔數" in feedback:
            target_holdings_count = 24
        if "現金水位" in feedback or "負數" in feedback:
            # 若現金不足或過低，下調總目標持股權重以留出更多現金緩衝
            target_total_stock_weight = min(target_total_stock_weight, 0.82)

    # 確保昨日所有持倉 (prev_holdings) 100% 都在候選池中（防止任何遺漏）
    focus_map: dict[str, FocusCandidate] = dict(funnel_out.focus_list)
    for held_ticker, sh in dp_state.prev_holdings.items():
        if sh > 0 and held_ticker not in focus_map and held_ticker in UNIVERSE_150:
            meta = UNIVERSE_150[held_ticker]
            price = funnel_out.close_prices.get(held_ticker, 100.0)
            cur_w = dp_state.holding_weights.get(held_ticker, 0.03)
            sd = ledger.get_or_create_stock(
                held_ticker, is_current_holding=True, current_weight=cur_w
            )
            obs_ids = funnel_out.ticker_to_obs_ids.get(
                held_ticker, [funnel_out.macro_obs_ids[0]]
            )
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
    # 1. 子模組 4.1：大盤姿態研判 (market_view)
    # -----------------------------------------------------------------------
    avg_chg = (
        sum(c.chg_pct for c in focus_map.values()) / len(focus_map)
        if focus_map
        else 0.0
    )
    if avg_chg >= 0.3 or dp_state.is_day1:
        regime = "risk_on"
        stance = "aggressive" if dp_state.is_day1 else "neutral"
    elif avg_chg <= -1.0:
        regime = "risk_off"
        stance = "defensive"
    else:
        regime = "neutral"
        stance = "neutral"

    market_view = {
        "basis_refs": list(funnel_out.macro_obs_ids[:10]),
        "logic": (
            f"依據總經指標與台股核心權值股前一交易日收盤數據（平均變動 {avg_chg:+.2f}%），"
            f"目前可交易淨值為 {dp_state.nav_tradable:,.0f} 元，帳上現金比例為 {dp_state.cash_ratio*100:.1f}%。"
            "依循本基金『主動台股供應鏈趨勢優選』之公開說明書理念，配置具備產業上下游傳導動能與流動性支撐之上市櫃標的，"
            "並將投組現金水位維持於 10%~20% 之安全緩衝區間，兼顧成長動能與下檔風險控制。"
        ),
        "regime": regime,
        "stance": stance,
        "posture": {
            "net_exposure_intent": "increase" if dp_state.is_day1 else "hold",
            "target_cash_pct_range": [0.08, 0.20],
        },
        "counter_evidence": "全球利率路徑與終端消費復甦節奏仍具不確定性，若權值股量縮回檔須適度控管單一標的曝險。",
        "confidence": 0.82,
    }

    # 嘗試呼叫 LLM 豐富市場觀點與深度論證 (保持 Schema 合規性)
    llm = get_llm("pm")
    thought_traces: list[dict[str, Any]] = []

    if llm:
        mv_prompt = f"""你是主動型台股 ETF 基金經理人。
請根據以下數據，撰寫市場總體研判邏輯 (20~400字繁體中文) 與 反方風險訊號 (10~200字繁體中文)。
【總經數據】: {[o['statement'] for o in funnel_out.observations if o['obs_id'] in funnel_out.macro_obs_ids]}
【可交易淨值】: {dp_state.nav_tradable:,.0f} 元，帳上現金比例: {dp_state.cash_ratio*100:.1f}%
【今日姿態】: {market_view['regime']}, {market_view['stance']}

請以 JSON 格式回傳，格式：
{{"logic": "...", "counter_evidence": "..."}}
注意：嚴禁 Markdown 標記，直接輸出合法 JSON。"""
        try:
            mv_res = llm.invoke(mv_prompt)
            raw_text = extract_text_content(mv_res.content).strip()
            thought_traces.append({"prompt": mv_prompt, "response": raw_text, "type": "market_view"})
            clean_json = raw_text.replace("```json", "").replace("```", "").strip()
            mv_data = json.loads(clean_json)
            if "logic" in mv_data and len(mv_data["logic"]) >= 20:
                market_view["logic"] = mv_data["logic"][:950]
            if "counter_evidence" in mv_data and mv_data["counter_evidence"]:
                market_view["counter_evidence"] = mv_data["counter_evidence"][:450]
        except Exception as exc:
            print(f"[WARN] LLM market_view enhancement failed: {exc}")

    # -----------------------------------------------------------------------
    # 2. 決定今日目標持倉名單 (控制在 22~26 檔，安全落在 20~30 檔正中央)
    # -----------------------------------------------------------------------
    held_list = [c for c in focus_map.values() if c.is_current_holding]
    new_cands = sorted(
        [c for c in focus_map.values() if not c.is_current_holding],
        key=lambda x: x.anomaly_score,
        reverse=True,
    )

    desired_count = max(21, min(28, target_holdings_count))
    kept_holdings: list[FocusCandidate] = []
    sell_all_holdings: list[FocusCandidate] = []

    if dp_state.is_day1:
        # Day 1：從 0 建倉，直接取前 desired_count 檔候選股（含 2330）
        buy_list = new_cands[:desired_count]
    else:
        # Day 2+：優先保留既有持股；若既有持股不足 20 檔則補買新股；若有明顯轉弱股且新股極強則執行最多 2 檔換股
        held_sorted = sorted(held_list, key=lambda x: x.anomaly_score, reverse=True)
        if len(held_sorted) > 28:
            kept_holdings = held_sorted[:26]
            sell_all_holdings = held_sorted[26:]
            buy_list = []
        elif len(held_sorted) < 21:
            kept_holdings = held_sorted
            need = desired_count - len(kept_holdings)
            buy_list = new_cands[:need]
        else:
            # 正常 21~28 檔：若新候選股有明顯正向事件訊號 (anomaly_score > 1.5)，可執行 1 檔汰弱留強
            strong_new = [c for c in new_cands if c.anomaly_score >= 1.5]
            if strong_new and len(held_sorted) < 26:
                kept_holdings = held_sorted
                buy_list = strong_new[:1]
            else:
                kept_holdings = held_sorted
                buy_list = []

    active_portfolio = kept_holdings + buy_list

    # -----------------------------------------------------------------------
    # 3. 依據 Dossier 可行範圍 [min_bound, max_bound] 計算目標權重並正規化
    # -----------------------------------------------------------------------
    raw_weights: dict[str, float] = {}
    for cand in active_portfolio:
        sd = ledger.get_or_create_stock(
            cand.ticker,
            chg_pct=cand.chg_pct,
            vol_ratio=cand.vol_ratio,
            is_current_holding=cand.is_current_holding,
            current_weight=cand.current_weight,
        )
        # 結合正反證據微調
        bull_cnt = len(sd.bull_evidences)
        bear_cnt = len(sd.bear_evidences)
        proposed = sd.weight_guide.base_value + 0.005 * (bull_cnt - bear_cnt)
        reason = (
            f"依據客觀價量與 {bull_cnt} 筆正向/{bear_cnt} 筆負向履歷證據微調權重"
            if (bull_cnt or bear_cnt)
            else "維持 Python 量化模型基準權重定位"
        )
        w = sd.weight_guide.clamp_and_set(proposed, reason)
        # 若為昨日自然超限個股，強制下調至安全上限內
        hard_cap = get_max_weight(cand.ticker) - 0.008
        raw_weights[cand.ticker] = min(w, hard_cap)

    total_raw = sum(raw_weights.values())
    scaled_weights: dict[str, float] = {}
    if total_raw > 0:
        scale = target_total_stock_weight / total_raw
        for t, w in raw_weights.items():
            hard_cap = get_max_weight(t) - 0.005
            scaled_weights[t] = round(min(w * scale, hard_cap), 4)

    # -----------------------------------------------------------------------
    # 4. 子模組 4.2 & 4.3：產生連續編號之 Inferences (I1..In) 與 Decisions (D1..Dk)
    # -----------------------------------------------------------------------
    inferences: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    no_trade_decisions: list[dict[str, Any]] = []

    def _add_inference_for_candidate(cand: FocusCandidate, action_desc: str) -> str:
        inf_id = f"I{len(inferences) + 1}"
        sd = ledger.get_or_create_stock(cand.ticker)
        premise = list(cand.obs_ids[:8]) if cand.obs_ids else [funnel_out.macro_obs_ids[0]]

        ev_note = ""
        if sd.bull_evidences:
            ev_note = f"同時參考外部正向事實（{sd.bull_evidences[-1].summary[:60]}），"
        counter_str = (
            sd.bear_evidences[-1].summary[:200]
            if sd.bear_evidences
            else f"若 {cand.name} 短期成交量能萎縮或供應鏈拉貨放緩，可能壓抑股價表現。"
        )

        inferences.append(
            {
                "inf_id": inf_id,
                "premise_refs": premise,
                "logic": (
                    f"{cand.ticker} ({cand.name}) 前一交易日收盤價為 {cand.close_price:.2f} 元"
                    f"（單日變動 {cand.chg_pct:+.2f}%，量比 {cand.vol_ratio:.2f} 倍）。"
                    f"{ev_note}綜合評估其於台股供應鏈之定位與可行權重區間 "
                    f"[{cand.weight_guide.min_bound:.3f}, {cand.weight_guide.max_bound:.3f}]，"
                    f"本基金決定採取「{action_desc}」策略配置。"
                ),
                "conclusion": f"{cand.ticker} ({cand.name}) 執行{action_desc}。",
                "counter_evidence": counter_str,
                "confidence": 0.78,
            }
        )
        return inf_id

    # 4a: 處理新建倉 (BUY)
    for cand in buy_list:
        tw = scaled_weights.get(cand.ticker, 0.032)
        inf_id = _add_inference_for_candidate(cand, f"新建倉買進至目標權重 {tw*100:.2f}%")
        decisions.append(
            {
                "decision_id": f"D{len(decisions) + 1}",
                "ticker": cand.ticker,
                "action": "BUY",
                "target_weight": tw,
                "inference_refs": [inf_id],
                "risk_check": f"單一權重 {tw*100:.2f}% 嚴格低於法定上限 {get_max_weight(cand.ticker)*100:.0f}%。",
            }
        )

    # 4b: 處理既有持倉保留或加減碼 (ADD / TRIM / no_trade_decisions)
    for cand in kept_holdings:
        tw = scaled_weights.get(cand.ticker, cand.current_weight)
        must_trim = cand.ticker in dp_state.overweight_tickers
        weight_diff = tw - cand.current_weight

        if must_trim:
            safe_tw = round(min(tw, get_max_weight(cand.ticker) - 0.01), 4)
            inf_id = _add_inference_for_candidate(
                cand, f"因權重超限執行風控減碼至 {safe_tw*100:.2f}%"
            )
            decisions.append(
                {
                    "decision_id": f"D{len(decisions) + 1}",
                    "ticker": cand.ticker,
                    "action": "TRIM",
                    "target_weight": safe_tw,
                    "inference_refs": [inf_id],
                    "risk_check": "落實 5 日寬限期內之單一權重上限風控減碼。",
                }
            )
        elif abs(weight_diff) >= 0.012 and cand.anomaly_score >= 1.5:
            act = "ADD" if weight_diff > 0 else "TRIM"
            inf_id = _add_inference_for_candidate(
                cand, f"動態{'加碼' if act == 'ADD' else '減碼'}至目標權重 {tw*100:.2f}%"
            )
            decisions.append(
                {
                    "decision_id": f"D{len(decisions) + 1}",
                    "ticker": cand.ticker,
                    "action": act,
                    "target_weight": tw,
                    "inference_refs": [inf_id],
                }
            )
        else:
            # 續抱不交易 (放入 no_trade_decisions)，確保高穩定度與低無謂周轉成本
            inf_id = _add_inference_for_candidate(
                cand, f"維持現有持股 {cand.current_shares} 股續抱"
            )
            no_trade_decisions.append(
                {
                    "ticker": cand.ticker,
                    "reason_refs": [inf_id],
                    "reason": "基本面與價量結構維持穩健，權重位於合理目標區間內，續抱不異動。",
                }
            )

    # 4c: 處理全數出清 (SELL_ALL)
    for cand in sell_all_holdings:
        inf_id = _add_inference_for_candidate(cand, "全數出清以優化投組持股檔數與資金效率")
        decisions.append(
            {
                "decision_id": f"D{len(decisions) + 1}",
                "ticker": cand.ticker,
                "action": "SELL_ALL",
                "target_weight": 0.0,
                "inference_refs": [inf_id],
            }
        )

    # -----------------------------------------------------------------------
    # 4d. 使用 LLM 為今日 20~26 檔重點決策批次生成真實產業鏈深度推論與因果鏈
    # -----------------------------------------------------------------------
    if llm and inferences:
        cand_items = []
        for inf in inferences:
            cand_items.append({
                "inf_id": inf["inf_id"],
                "stock": inf["conclusion"],
                "current_logic": inf["logic"],
            })
        batch_prompt = f"""你是主動型台股 ETF 經理人。請針對以下持股決策清單，深化其產業鏈因果邏輯 (logic: 30~250字) 與 反方風險訊號 (counter_evidence: 15~150字)。
【持股決策清單】:
{json.dumps(cand_items[:28], ensure_ascii=False, indent=2)}

請回傳合法 JSON 陣列，格式如下：
[
  {{"inf_id": "I1", "logic": "...", "counter_evidence": "..."}},
  ...
]
注意：嚴禁任何 Markdown 標籤，直接回傳標準 JSON 陣列。"""
        try:
            batch_res = llm.invoke(batch_prompt)
            batch_text = extract_text_content(batch_res.content).strip()
            thought_traces.append({"prompt": batch_prompt, "response": batch_text, "type": "inferences_batch"})
            clean_batch = batch_text.replace("```json", "").replace("```", "").strip()
            refined_list = json.loads(clean_batch)
            refined_map = {item["inf_id"]: item for item in refined_list if isinstance(item, dict) and "inf_id" in item}
            for inf in inferences:
                iid = inf["inf_id"]
                if iid in refined_map:
                    ref = refined_map[iid]
                    if "logic" in ref and len(str(ref["logic"])) >= 20:
                        inf["logic"] = str(ref["logic"])[:950]
                    if "counter_evidence" in ref and ref["counter_evidence"]:
                        inf["counter_evidence"] = str(ref["counter_evidence"])[:450]
        except Exception as exc:
            print(f"[WARN] LLM inferences batch enhancement failed: {exc}")

    # 存檔思考軌跡供 12/2 程式碼審查 (以獨立檔案保存，嚴禁污染 D-Plan JSON 欄位)
    trace_dir = Path("runs") / dp_state.trade_date
    trace_dir.mkdir(parents=True, exist_ok=True)
    with (trace_dir / "thought_trace.jsonl").open("a", encoding="utf-8") as tf:
        for tr in thought_traces:
            tf.write(json.dumps(tr, ensure_ascii=False) + "\n")

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
        "orders": [],  # 交由 M5 derive_orders_and_normalize 機械推導
        "agent_metadata": {
            "model_provider": "google",
            "model_version": "gemini-3.8-flash",
            "run_started_at": started_at,
            "run_completed_at": completed_at,
            "code_version": code_version[:64],
        },
    }
