"""測試共用設定：所有測試一律離線，禁止呼叫真實 LLM 或外部網路。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    """強制 PM Agent 走 stub（get_llm 回 None），避免測試消耗 API 額度或卡住。"""
    monkeypatch.setattr("etf_agent.pm_agent.get_llm", lambda role: None)
    monkeypatch.setattr("etf_agent.decision.get_llm", lambda role: None)
    monkeypatch.setattr("etf_agent.llm.get_llm", lambda role: None)
