"""④ 决策子图:提取 → 多方 → 空方 → 裁判 → 硬规则。

主图只把它当成一个节点。多 Agent 的复杂度全部关在这里,不会让主图爆炸。
未来要加角色(风险官、因果白板 Agent…),在这里加节点即可。

目前所有节点都是 stub:没有 API key 时直接产生固定文字,流程照样跑完。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from etf_agent import plugins, rules
from etf_agent.llm import get_llm
from etf_agent.state import AgentState


def make_retrieve(store):
    def retrieve(state: AgentState) -> dict:
        """提取:合并所有提取外挂的结果(依 id 去重),并保留错误。"""
        results, errors = plugins.run_all("retriever", state["as_of"], store)
        unique = list({r["id"]: r for r in results}.values())
        return {"retrieved": unique, "errors": errors}

    return retrieve


def _argue(side: str, state: AgentState) -> dict:
    evidence = state.get("retrieved", [])
    llm = get_llm(side)
    if llm is None:  # stub
        text = f"(stub) {side} 方阅读了 {len(evidence)} 条证据,尚未接上模型。"
    else:
        brief = "\n".join(f"- [{e['stance']}] {e['summary']}" for e in evidence)
        stance_hint = "看多" if side == "bull" else "看空"
        text = llm.invoke(f"你是{stance_hint}分析师,请依据下列证据提出论述:\n{brief}").content
    return {"arguments": [{"side": side, "text": text}]}


def bull(state: AgentState) -> dict:
    return _argue("bull", state)


def bear(state: AgentState) -> dict:
    return _argue("bear", state)


def judge(state: AgentState) -> dict:
    """裁判:综合多空论述,提出调仓。stub 版不调仓。"""
    # TODO(组员): 接上 get_llm("judge"),输出 trades 清单。
    return {"decision": {"trades": [], "rationale": "(stub) 尚未接上模型,维持不动。"}}


def enforce_rules(state: AgentState) -> dict:
    """硬规则:无论 AI 怎么说,都在这里被锁住。"""
    decision = dict(state.get("decision", {"trades": [], "rationale": ""}))
    trades = list(decision.get("trades", []))
    if len(trades) > rules.MAX_TRADES_PER_DAY:
        decision["rationale"] += f"\n[硬规则] 调仓超过每日上限 {rules.MAX_TRADES_PER_DAY},已截断。"
        trades = trades[: rules.MAX_TRADES_PER_DAY]
    decision["trades"] = trades
    return {"decision": decision}


def build_decide_subgraph(store, wrap):
    """wrap = 外层传入的「安全包装」,让每个节点都有错误隔离与追溯。"""
    g = StateGraph(AgentState)
    g.add_node("retrieve", wrap("retrieve", make_retrieve(store)))
    g.add_node("bull", wrap("bull", bull))
    g.add_node("bear", wrap("bear", bear))
    g.add_node("judge", wrap("judge", judge))
    g.add_node("enforce_rules", wrap("enforce_rules", enforce_rules))
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "bull")
    g.add_edge("bull", "bear")
    g.add_edge("bear", "judge")
    g.add_edge("judge", "enforce_rules")
    g.add_edge("enforce_rules", END)
    return g.compile()
