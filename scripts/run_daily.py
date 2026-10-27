"""每日一鍵執行：DataPack -> 抓行情 -> 漏斗 -> PM Agent ⇄ 沙盒 -> D-Plan JSON。

用法：
    python scripts/run_daily.py <DataPack.json> [--offline]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from etf_agent.collectors import (  # noqa: E402
    fetch_macro_indicators,
    fetch_market_quotes,
    load_extra_signals,
    merge_quotes_with_datapack,
    parse_datapack,
)
from etf_agent.dashboard import generate_html_dashboard  # noqa: E402
from etf_agent.graph import run_dplan_pipeline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("datapack")
    ap.add_argument("--offline", action="store_true", help="不連網，使用預設報價（僅測試用）")
    args = ap.parse_args()

    dp = parse_datapack(args.datapack)
    out_dir = ROOT / "runs" / dp.trade_date
    snap = out_dir / "source_snapshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    quotes, macros = None, None
    if not args.offline:
        quotes = fetch_market_quotes(snapshot_dir=snap)
        macros = fetch_macro_indicators(snapshot_dir=snap)
        print(f"[OK] 抓到 {len(quotes)}/150 檔報價, {len(macros)} 筆總經指標")
    quotes = merge_quotes_with_datapack(dp, quotes)

    # 讀取 30 檔主動 ETF 基準 (inputs/etf_benchmarks.json) 執行真實 Active Share 檢核
    benchmarks_file = ROOT / "inputs" / "etf_benchmarks.json"
    etf_benchmarks = json.loads(benchmarks_file.read_text(encoding="utf-8")) if benchmarks_file.exists() else None

    state = run_dplan_pipeline(
        datapack_input=args.datapack,
        external_quotes=quotes,
        macros=macros,
        extra_signals=load_extra_signals(ROOT / "inputs" / "extra_signals"),
        etf_benchmarks=etf_benchmarks,
        output_dir=out_dir,
    )
    report = state["report"]
    print("[OK] 沙盒驗證通過" if report.passed else "[FAIL] 沙盒驗證未通過")
    if not report.passed:
        print(report.format_feedback_for_agent())
        return 1
    print(f"[OK] D-Plan: {state['dplan_path']}")
    html_path = generate_html_dashboard(state['dplan_path'])
    print(f"[OK] 視覺化儀表板已產出: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
