"""命令列入口: uv run python -m etf_agent --as-of 2026-01-05"""

import argparse

from etf_agent.graph import run


def main() -> None:
    p = argparse.ArgumentParser(description="执行一次 ETF Agent 流程")
    p.add_argument("--as-of", required=True, help="截至时间,例如 2026-01-05 或 2026-01-05T09:00:00")
    p.add_argument("--trigger", default="manual", help="触发方式(仅记录): daily/earnings/manual")
    p.add_argument("--db", default="data/evidence.db")
    a = p.parse_args()
    final = run(a.as_of, a.trigger, a.db)
    print(f"完成。报告: {final['report_path']}")
    for e in final.get("errors", []):
        print("⚠", e)


if __name__ == "__main__":
    main()
