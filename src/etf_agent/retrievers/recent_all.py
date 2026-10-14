"""示范「提取」外挂:决定决策前要把哪些证据交给 AI(概要层)。

回传 list[dict],每个 dict 就是 `store.search()` 的一笔结果。
多个提取外挂的结果会合并并依 id 去重。
store.search 已自动套用 as_of,你无法(也不需要)自己处理前视偏差。
"""

from etf_agent.plugins import retriever


@retriever("recent_all")
def recent_all(as_of: str, store) -> list[dict]:
    return store.search(as_of=as_of, limit=30)
