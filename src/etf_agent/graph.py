"""主圖與閉環管線 (`graph.py`)：串接 M1 ~ M5 並執行『PM Agent ⇄ Sandbox 守門員』自我修正迴圈。

包含兩套入口：
1. `run_dplan_pipeline(...)`（正式競賽主線）：
   DataPack 解析 (M2) ➔ 候選漏斗與 S/O 編號 (M3) ➔ PM Agent 決策 (M4) ⇄ Sandbox 八大鐵律驗算與退回補齊 (M5)
   ➔ 產出 100% 合規之 `D-Plan_<team_id>_<trade_date>.json` 與戰情摘要。
2. `run(...)`（保留舊版課程測試介面，相容 `tests/test_pipeline.py`）。
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from etf_agent import plugins
from etf_agent.collectors import (
    DataPackState,
    ExtraSignalCard,
    MacroRecord,
    QuoteRecord,
    parse_datapack,
)
from etf_agent.decision import build_decide_subgraph
from etf_agent.dossier import DossierLedger
from etf_agent.funnel import FunnelOutput, build_funnel_and_observations
from etf_agent.memory import EvidenceStore
from etf_agent.pm_agent import generate_or_repair_dplan_draft
from etf_agent.sandbox import (
    VerificationReport,
    align_posture_and_funding,
    derive_orders_and_normalize,
    verify_dplan,
)
from etf_agent.state import AgentState


# ===========================================================================
# 正式競賽閉環管線 (M1 -> M2 -> M3 -> M4 ⇄ M5)
# ===========================================================================
class DPlanGraphState(TypedDict, total=False):
    datapack_input: dict[str, Any] | str
    external_quotes: dict[str, QuoteRecord] | None
    macros: list[MacroRecord] | None
    extra_signals: list[ExtraSignalCard] | None
    etf_benchmarks: dict[str, dict[str, float]] | None
    ledger: DossierLedger
    dp_state: DataPackState
    funnel_out: FunnelOutput
    dplan: dict[str, Any]
    report: VerificationReport
    sandbox_feedback: str | None
    thought_traces: list[dict[str, Any]]
    retry_count: int
    max_retries: int
    output_dir: str | None
    dplan_path: str | None


def _node_collect_and_funnel(state: DPlanGraphState) -> dict[str, Any]:
    """節點 1 (M2 + M3)：解析 DataPack、更新連結履歷、建立 Focus List 與 S1..Sn / O1..On。"""
    ledger = state.get("ledger") or DossierLedger()
    dp_state = parse_datapack(state["datapack_input"])
    funnel_out = build_funnel_and_observations(
        dp_state=dp_state,
        external_quotes=state.get("external_quotes"),
        macros=state.get("macros"),
        extra_signals=state.get("extra_signals"),
        ledger=ledger,
    )
    return {
        "ledger": ledger,
        "dp_state": dp_state,
        "funnel_out": funnel_out,
        "retry_count": 0,
        "sandbox_feedback": None,
    }


def _node_pm_agent_draft(state: DPlanGraphState) -> dict[str, Any]:
    """節點 2 (M4)：產生或根據沙盒退回指引補齊 D-Plan 決策草案。"""
    dplan_draft = generate_or_repair_dplan_draft(
        dp_state=state["dp_state"],
        funnel_out=state["funnel_out"],
        ledger=state["ledger"],
        feedback=state.get("sandbox_feedback"),
    )
    # 取出非 Schema 的思考軌跡（跨重試累積），避免污染 D-Plan 並供 finalize 存檔
    new_traces = dplan_draft.pop("_thought_traces", [])
    return {
        "dplan": dplan_draft,
        "thought_traces": list(state.get("thought_traces") or []) + new_traces,
    }


def _node_sandbox_verify(state: DPlanGraphState) -> dict[str, Any]:
    """節點 3 (M5)：機械推導 orders、對齊 posture 與 funding_for，並執行八大鐵律驗算。"""
    dp_state = state["dp_state"]
    funnel_out = state["funnel_out"]
    draft = state["dplan"]

    # 5.1 機械推導千股委託單 + 0 股自動移回 no_trade_decisions
    normalized = derive_orders_and_normalize(
        dplan=draft,
        nav_tradable=dp_state.nav_tradable,
        prev_holdings=dp_state.prev_holdings,
        close_prices=funnel_out.close_prices,
    )
    # 5.3 自動對齊 posture 現金區間與拔除多餘 funding_for
    aligned = align_posture_and_funding(
        dplan=normalized,
        prev_cash=dp_state.cash,
        nav_tradable=dp_state.nav_tradable,
        close_prices=funnel_out.close_prices,
    )
    # 5.2 八大鐵律驗算
    report = verify_dplan(
        dplan=aligned,
        nav_tradable=dp_state.nav_tradable,
        prev_cash=dp_state.cash,
        prev_holdings=dp_state.prev_holdings,
        close_prices=funnel_out.close_prices,
        etf_benchmarks=state.get("etf_benchmarks"),
    )
    feedback = None if report.passed else report.format_feedback_for_agent()
    return {
        "dplan": aligned,
        "report": report,
        "sandbox_feedback": feedback,
        "retry_count": state.get("retry_count", 0) + 1,
    }


def _route_after_verify(state: DPlanGraphState) -> str:
    """條件路由：若未通過且未達重試上限，退回 PM Agent 重新思考補齊；通過則輸出存檔。"""
    report = state["report"]
    if report.passed:
        return "finalize"
    if state.get("retry_count", 0) <= state.get("max_retries", 2):
        return "retry_pm_agent"
    return "finalize"


def _node_finalize(state: DPlanGraphState) -> dict[str, Any]:
    """節點 4：將通過八大鐵律驗算的 D-Plan 寫入硬碟，並存檔連結履歷。"""
    out_dir = state.get("output_dir")
    if not out_dir:
        return {"dplan_path": None}

    dp_state = state["dp_state"]
    dplan = dict(state["dplan"])
    
    p_dir = Path(out_dir)
    p_dir.mkdir(parents=True, exist_ok=True)
    
    # 存檔 D-Plan.json
    file_path = p_dir / f"D-Plan_{dp_state.team_id}_{dp_state.trade_date}.json"
    file_path.write_text(
        json.dumps(dplan, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 存檔思考軌跡 thought_trace.jsonl（Prompt 與 LLM 原始回覆，供 12/2 審查）
    with (p_dir / "thought_trace.jsonl").open("w", encoding="utf-8") as f:
        for trace in state.get("thought_traces") or []:
            f.write(json.dumps(trace, ensure_ascii=False) + "\n")

    # 存檔連結履歷 Ledger
    ledger = state.get("ledger")
    if ledger:
        ledger.save(p_dir / "dossier_ledger.json")

    return {"dplan_path": str(file_path)}


def build_dplan_graph():
    """編譯正式競賽 D-Plan LangGraph 狀態機。"""
    g = StateGraph(DPlanGraphState)
    g.add_node("collect_and_funnel", _node_collect_and_funnel)
    g.add_node("pm_agent_draft", _node_pm_agent_draft)
    g.add_node("sandbox_verify", _node_sandbox_verify)
    g.add_node("finalize", _node_finalize)

    g.add_edge(START, "collect_and_funnel")
    g.add_edge("collect_and_funnel", "pm_agent_draft")
    g.add_edge("pm_agent_draft", "sandbox_verify")
    g.add_conditional_edges(
        "sandbox_verify",
        _route_after_verify,
        {
            "retry_pm_agent": "pm_agent_draft",
            "finalize": "finalize",
        },
    )
    g.add_edge("finalize", END)
    return g.compile()


def run_dplan_pipeline(
    datapack_input: dict[str, Any] | str | Path,
    external_quotes: dict[str, QuoteRecord] | None = None,
    macros: list[MacroRecord] | None = None,
    extra_signals: list[ExtraSignalCard] | None = None,
    etf_benchmarks: dict[str, dict[str, float]] | None = None,
    ledger: DossierLedger | None = None,
    output_dir: str | Path | None = None,
    max_retries: int = 2,
) -> DPlanGraphState:
    """一鍵執行 M1~M5 閉環管線，回傳包含最終 `dplan`、`report` 與 `dplan_path` 的完整狀態。"""
    app = build_dplan_graph()
    init_state: DPlanGraphState = {
        "datapack_input": str(datapack_input)
        if isinstance(datapack_input, Path)
        else datapack_input,
        "external_quotes": external_quotes,
        "macros": macros,
        "extra_signals": extra_signals,
        "etf_benchmarks": etf_benchmarks,
        "ledger": ledger or DossierLedger(),
        "output_dir": str(output_dir) if output_dir else None,
        "max_retries": max_retries,
    }
    return app.invoke(init_state)


# ===========================================================================
# 舊版課程測試相容介面 (保留供 tests/test_pipeline.py 使用)
# ===========================================================================
_counter = {"n": 0}


def safe(name: str, fn):
    """包裝節點：錯誤隔離 + 追溯輸出。"""

    def wrapped(state: AgentState) -> dict:
        try:
            out = fn(state) or {}
        except Exception as exc:  # noqa: BLE001
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            out = {
                "errors": [
                    f"[節點 {name}] 執行失敗 → {type(exc).__name__}: {exc} "
                    f"(位置: {tb.filename}:{tb.lineno})。已跳過此節點,流程繼續。"
                ]
            }
        run_dir = state.get("run_dir")
        if run_dir:
            _counter["n"] += 1
            path = Path(run_dir) / f"{_counter['n']:02d}_{name}.json"
            path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), "utf-8")
        return out

    return wrapped


def make_observe(store: EvidenceStore):
    def observe(state: AgentState) -> dict:
        items, errors = plugins.run_all("observer", state["as_of"])
        ids = [ev.id for ev in items if store.add(ev)]
        return {"new_evidence_ids": ids, "errors": errors}

    return observe


def record(state: AgentState) -> dict:
    d = state.get("decision", {"trades": [], "rationale": "(无决策)"})
    lines = [
        f"# 每日调仓报告 {state['as_of'][:10]}",
        f"触发方式: {state.get('trigger', 'manual')}",
        f"新增证据: {len(state.get('new_evidence_ids', []))} 条 / "
        f"本次阅读: {len(state.get('retrieved', []))} 条",
        "\n## 多空论述",
        *[f"- **{a['side']}**: {a['text']}" for a in state.get("arguments", [])],
        "\n## 调仓",
        *([f"- {t}" for t in d["trades"]] or ["- 今日不调仓"]),
        f"\n理由: {d['rationale']}",
        "\n## 错误与警告",
        *([f"- {e}" for e in state.get("errors", [])] or ["- 无"]),
    ]
    path = Path(state["run_dir"]) / "report.md"
    path.write_text("\n".join(lines), "utf-8")
    return {"report_path": str(path)}


def build_graph(store: EvidenceStore):
    g = StateGraph(AgentState)
    g.add_node("observe", safe("observe", make_observe(store)))
    g.add_node("decide", build_decide_subgraph(store, safe))
    g.add_node("record", safe("record", record))
    g.add_edge(START, "observe")
    g.add_edge("observe", "decide")
    g.add_edge("decide", "record")
    g.add_edge("record", END)
    return g.compile()


def run(
    as_of: str,
    trigger: str = "manual",
    db_path: str = "data/evidence.db",
    runs_root: str = "runs",
) -> dict:
    _counter["n"] = 0
    run_dir = Path(runs_root) / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(db_path)
    return build_graph(store).invoke(
        {"as_of": as_of, "trigger": trigger, "run_dir": str(run_dir)}
    )
