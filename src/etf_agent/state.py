"""共用状态 (State) —— 全队的「契约」。

每个节点只做一件事:读取它需要的栏位,回传它负责更新的栏位。
改动这个档案会影响所有人,请先在 Discord 讨论。

带 `Annotated[list, operator.add]` 的栏位表示「多个节点可以各自追加,系统自动合并」。
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    # ---- ① 触发:为什么跑、截至哪一天 ----
    as_of: str  # 截至时间 ISO 格式。所有查询只能看到此时间之前「可见」的证据
    trigger: str  # "daily" / "earnings" / "manual" …仅供记录,图本身不在乎
    run_dir: str  # 本次执行的追溯资料夹(自动产生)

    # ---- ② 观察:新进来的证据 ----
    new_evidence_ids: Annotated[list[str], operator.add]

    # ---- ③ 提取:决策前交给 AI 阅读的证据(概要层) ----
    retrieved: list[dict]

    # ---- ④ 决策:多空论述与最终调仓 ----
    arguments: Annotated[list[dict], operator.add]  # [{"side": "bull", "text": ...}, ...]
    decision: dict  # {"trades": [{"ticker","action","weight"}], "rationale": str}

    # ---- 记录 ----
    errors: Annotated[list[str], operator.add]  # 友善错误讯息(哪个节点、哪里出错)
    report_path: str
