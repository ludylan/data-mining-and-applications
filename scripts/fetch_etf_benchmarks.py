"""從 MoneyDJ 免費爬取官方指定的 30 檔主動型 ETF 前 15 大持股權重，並輸出為 inputs/etf_benchmarks.json。"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

from etf_agent.universe import ACTIVE_ETFS_30

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = ROOT / "inputs" / "etf_benchmarks.json"

# 精確鎖定持股表格：排除側邊欄的熱門 ETF 排行 (0050.TW 等)，僅匹配持股明細
PATTERN_TW = re.compile(
    r'<a\s+href=[\'"][^\'"]*/etf/ea/ETZCW\.djhtm[^\'"]*etfid=([0-9]{4})\.TW[^\'"]*[\'"][^>]*>.*?</a>\s*</td>\s*<td[^>]*>([0-9.]+)</td>',
    re.DOTALL | re.IGNORECASE,
)
PATTERN_US = re.compile(
    r'<a\s+href=[\'"][^\'"]*/etf/ea/ETZCW\.djhtm[^\'"]*etfid=([A-Z0-9.]+)\.US[^\'"]*[\'"][^>]*>.*?</a>\s*</td>\s*<td[^>]*>([0-9.]+)</td>',
    re.DOTALL | re.IGNORECASE,
)


def fetch_etf_holdings(etf_code: str, timeout: float = 8.0) -> dict[str, float]:
    url = f"https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid={etf_code}.TW"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            # 優先匹配台股代號 (4 位數字)
            tw_matches = PATTERN_TW.findall(html)
            holdings: dict[str, float] = {}
            for ticker, pct in tw_matches:
                w = round(float(pct) / 100.0, 4)
                if w > 0:
                    holdings[ticker] = w
            
            # 若為海外/美股科技型主動 ETF (如 00989A)，匹配美股代號
            if not holdings:
                us_matches = PATTERN_US.findall(html)
                for ticker, pct in us_matches:
                    w = round(float(pct) / 100.0, 4)
                    if w > 0:
                        holdings[ticker] = w

            # 只取前 15 大成分股 (官方比對標準)
            sorted_top15 = sorted(holdings.items(), key=lambda x: x[1], reverse=True)[:15]
            return dict(sorted_top15)
    except Exception as exc:
        print(f"[WARN] Fetch {etf_code} failed: {exc}")
        return {}


def main() -> int:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    benchmarks: dict[str, dict[str, float]] = {}
    print(f"開始抓取 30 檔主動型 ETF 持股權重 (來源: MoneyDJ)...")

    for idx, (code, name) in enumerate(ACTIVE_ETFS_30.items(), 1):
        top15 = fetch_etf_holdings(code)
        if top15:
            benchmarks[code] = top15
            print(f"[{idx}/30] {code} ({name}): 成功抓取前 {len(top15)} 大持股 (Top 1: {list(top15.keys())[0]} {list(top15.values())[0]*100:.1f}%)")
        else:
            print(f"[{idx}/30] {code} ({name}): 未抓取到持股或海外型標的")
        time.sleep(0.3)  # 禮貌延遲防限流

    OUTPUT_FILE.write_text(json.dumps(benchmarks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] 成功將 {len(benchmarks)}/30 檔基準持股存入: {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
