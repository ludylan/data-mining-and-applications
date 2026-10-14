"""记忆层:证据库 (SQLite + FTS5)。

设计重点:
1. 一条「证据」= 一段可追溯的资讯,有三层内容(概要 → 要点 → 原文)。
2. `stance`(立场)只用来分群检索,**不计票、不加总**,多空由决策 Agent 综合判断。
3. 所有查询都必须带 `as_of`,只会看到「可见时间 <= as_of」的证据 → 防前视偏差的唯一防线。
4. 只增不改。想换成向量库或别的储存,只要保留这个类别的方法签名即可。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

STANCES = ("bull", "bear", "uncertain")  # 偏多 / 偏空 / 不确定。想细分请先在 README 讨论


@dataclass
class Evidence:
    id: str  # 唯一编号,例如 "news-2026-01-05-001"
    source: str  # 来源(网址、财报名称…)
    published_at: str  # 原始发布时间 ISO 格式 "2026-01-05T08:30:00"
    visible_at: str  # 系统「可以看到」的时间(通常 >= published_at)。查询以此为准
    summary: str  # 第 1 层:一句话概要
    detail: str = ""  # 第 2 层:数据与要点
    passage: str = ""  # 第 3 层:原文段落
    stance: str = "uncertain"  # bull / bear / uncertain
    tickers: list[str] = field(default_factory=list)  # 相关标的或 ETF
    topics: list[str] = field(default_factory=list)  # 自由主题标签
    context: str = ""  # 上下文标记,例如「2025Q4 财报 / 营收章节」


class EvidenceStore:
    def __init__(self, path: str | Path = "data/evidence.db"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY, source TEXT, published_at TEXT, visible_at TEXT,
                summary TEXT, detail TEXT, passage TEXT, stance TEXT,
                tickers TEXT, topics TEXT, context TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_visible ON evidence(visible_at);
            CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
                id UNINDEXED, summary, detail, passage, tokenize='trigram'
            );
            """
        )

    # ---------- 写入 ----------
    def add(self, ev: Evidence) -> bool:
        """写入证据。id 重复则略过(只增不改),回传是否为新增。"""
        if ev.stance not in STANCES:
            raise ValueError(f"stance 必须是 {STANCES} 其中之一,收到 {ev.stance!r}")
        if ev.visible_at < ev.published_at:
            raise ValueError("visible_at 不可早于 published_at(会造成前视偏差)")
        d = asdict(ev)
        d["tickers"], d["topics"] = json.dumps(d["tickers"]), json.dumps(d["topics"])
        try:
            self.db.execute(
                "INSERT INTO evidence VALUES (:id,:source,:published_at,:visible_at,:summary,"
                ":detail,:passage,:stance,:tickers,:topics,:context)",
                d,
            )
        except sqlite3.IntegrityError:
            return False
        self.db.execute(
            "INSERT INTO evidence_fts VALUES (?,?,?,?)",
            (ev.id, ev.summary, ev.detail, ev.passage),
        )
        self.db.commit()
        return True

    # ---------- 提取(AI 的三个动作:搜、看、展开) ----------
    def search(
        self,
        as_of: str,
        query: str | None = None,
        stance: str | None = None,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """搜寻证据,只回传「概要层」。as_of 必填,且永远生效。"""
        sql = "SELECT e.* FROM evidence e WHERE e.visible_at <= ?"
        args: list = [as_of]
        if query:
            if len(query) >= 3:  # trigram 全文检索需 >= 3 字
                sql += " AND e.id IN (SELECT id FROM evidence_fts WHERE evidence_fts MATCH ?)"
                args.append('"' + query.replace('"', "") + '"')
            else:
                sql += " AND (e.summary LIKE ? OR e.detail LIKE ? OR e.passage LIKE ?)"
                args += [f"%{query}%"] * 3
        if stance:
            sql += " AND e.stance = ?"
            args.append(stance)
        if ticker:
            sql += " AND e.tickers LIKE ?"
            args.append(f'%"{ticker}"%')
        sql += " ORDER BY e.visible_at DESC LIMIT ?"
        args.append(limit)
        return [self._brief(r) for r in self.db.execute(sql, args)]

    def get(self, evidence_id: str, as_of: str, level: str = "detail") -> dict | None:
        """展开单条证据。level: summary / detail / passage。同样受 as_of 限制。"""
        r = self.db.execute(
            "SELECT * FROM evidence WHERE id = ? AND visible_at <= ?", (evidence_id, as_of)
        ).fetchone()
        if r is None:
            return None
        out = self._brief(r)
        if level in ("detail", "passage"):
            out["detail"] = r["detail"]
        if level == "passage":
            out["passage"] = r["passage"]
        return out

    @staticmethod
    def _brief(r: sqlite3.Row) -> dict:
        return {
            "id": r["id"],
            "summary": r["summary"],
            "stance": r["stance"],
            "tickers": json.loads(r["tickers"]),
            "topics": json.loads(r["topics"]),
            "context": r["context"],
            "source": r["source"],
            "published_at": r["published_at"],
            "visible_at": r["visible_at"],
        }
