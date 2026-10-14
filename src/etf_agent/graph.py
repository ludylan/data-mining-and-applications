"""主图:观察 → 决策(子图) → 记录。

这是整个专案唯一的「接线图」。组员平常不需要改这里:
新增观察 → 放进 observers/;新增提取 → 放进 retrievers/。

每个节点都被 `safe` 包住,提供两件事:
1. 错误隔离:节点出错时,不中断流程,错误以友善讯息写入 errors。
2. 追溯:每个节点的输出都存成 runs/<时间>/NN_节点名.json,事后可回看。
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from etf_agent import plugins
from etf_agent.decision import build_decide_subgraph
from etf_agent.memory import EvidenceStore
from etf_agent.state import AgentState

_counter = {"n": 0}


def safe(name: str, fn):
    """包装节点:错误隔离 + 追溯输出。"""

    def wrapped(state: AgentState) -> dict:
        try:
            out = fn(state) or {}
        except Exception as exc:  # noqa: BLE001
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            out = {
                "errors": [
                    f"[节点 {name}] 执行失败 → {type(exc).__name__}: {exc} "
                    f"(位置: {tb.filename}:{tb.lineno})。已跳过此节点,流程继续。"
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
        """观察:执行所有观察外挂,把证据写入记忆库。"""
        items, errors = plugins.run_all("observer", state["as_of"])
        ids = [ev.id for ev in items if store.add(ev)]
        return {"new_evidence_ids": ids, "errors": errors}

    return observe


def record(state: AgentState) -> dict:
    """记录:每天必交的调仓报告。即使前面有节点出错,也一定会产生报告。"""
    d = state.get("decision", {"trades": [], "rationale": "(无决策)"})
    lines = [
        f"# 每日调仓报告 {state['as_of'][:10]}",
        f"触发方式: {state.get('trigger', 'manual')}",
        f"新增证据: {len(state.get('new_evidence_ids', []))} 条 / "
        f"本次阅读: {len(state.get('retrieved', []))} 条",
        "\n## 多空论述",
        *[f"- **{a['side']}**: {a['text']}" for a in state.get("arguments", [])],
        "\n## 调仓",
        *(
            [f"- {t}" for t in d["trades"]]
            or ["- 今日不调仓"]
        ),
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
    g.add_node("decide", build_decide_subgraph(store, safe))  # 子图当作一个节点
    g.add_node("record", safe("record", record))
    g.add_edge(START, "observe")
    g.add_edge("observe", "decide")
    g.add_edge("decide", "record")
    g.add_edge("record", END)
    return g.compile()


def run(as_of: str, trigger: str = "manual", db_path: str = "data/evidence.db",
        runs_root: str = "runs") -> dict:
    """执行一次完整流程,回传最终状态。"""
    _counter["n"] = 0
    run_dir = Path(runs_root) / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(db_path)
    return build_graph(store).invoke(
        {"as_of": as_of, "trigger": trigger, "run_dir": str(run_dir)}
    )
