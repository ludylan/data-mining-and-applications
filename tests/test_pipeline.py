"""测试:1) 整条流程能跑  2) 防前视偏差  3) 外挂出错不拖垮流程。"""

import pytest

from etf_agent import plugins
from etf_agent.graph import run
from etf_agent.memory import Evidence, EvidenceStore


def _ev(id_, visible, published=None):
    return Evidence(id=id_, source="t", published_at=published or visible, visible_at=visible,
                    summary="营收低于预期")


def test_smoke_pipeline(tmp_path):
    """整条流程(全是 stub)能跑完,并产生报告。"""
    final = run("2026-01-05", db_path=str(tmp_path / "e.db"), runs_root=str(tmp_path / "runs"))
    assert "每日调仓报告" in open(final["report_path"], encoding="utf-8").read()
    assert final["errors"] == []


def test_no_lookahead():
    """as_of 之前不可见的证据,搜寻与展开都不得看到。"""
    s = EvidenceStore(":memory:")
    s.add(_ev("a", "2026-01-05T08:00:00"))
    s.add(_ev("b", "2026-01-06T08:00:00"))
    assert [r["id"] for r in s.search("2026-01-05T23:59:59")] == ["a"]
    assert s.get("b", "2026-01-05T23:59:59") is None
    assert s.search("2026-01-05T23:59:59", query="营收")  # 关键字检索可用


def test_visible_before_published_rejected():
    s = EvidenceStore(":memory:")
    with pytest.raises(ValueError):
        s.add(_ev("x", "2026-01-01T00:00:00", published="2026-01-02T00:00:00"))


def test_broken_plugin_does_not_break_pipeline(tmp_path):
    """某个观察外挂炸掉,流程照样完成,错误出现在 errors。"""

    @plugins.observer("_broken_for_test")
    def broken(as_of):
        raise RuntimeError("故意弄坏")

    try:
        final = run("2026-01-05", db_path=str(tmp_path / "e.db"), runs_root=str(tmp_path / "r"))
        assert any("_broken_for_test" in e for e in final["errors"])
        assert final["report_path"]
    finally:
        plugins._REGISTRY["observer"].pop("_broken_for_test", None)
