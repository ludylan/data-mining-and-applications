"""模組 2 (M2)：資料收集與官方 DataPack 解析層 (`collectors.py`)。

對應上游規格書：`docs/specs/M2_collectors_spec.md` 與 `docs/DataPack_規格書_v4.3.md`。

核心職責（「我今天手上有什麼、市場看到什麼」）：
1. 子模組 2.1 (`parse_datapack`)：
   - 解析官方每日 `DataPack.json`，整理昨日結算持倉 (`prev_holdings`)、可用現金 (`cash`)、可交易淨值 (`nav_tradable`)。
   - **未結算攔截**：若 `"prev_settlement" in missing` 或 `account is None`，立即拋出 `SettlementNotReadyError` 阻止盲目下單。
   - **首日 vs 日常判別**：自動標記 `is_day1`（全現金 10 億、0 持股），並偵測是否有因自然上漲超限 (>10% 或 >25%) 需減碼的個股。
2. 子模組 2.2 ~ 2.4 (`QuoteRecord`, `MacroRecord`, `ExtraSignalCard`)：
   - 將 150 檔收盤行情、總經指標（費半/那指/台積電ADR/加權指數）與隊友外掛訊號統一轉為附帶合法 `authority`、`url` 與 `+08:00` 時間戳記的標準結構。
"""

from __future__ import annotations

import csv
import json
import ssl
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from etf_agent.universe import UNIVERSE_150, get_authority, get_max_weight, is_valid_ticker


class SettlementNotReadyError(RuntimeError):
    """當官方 DataPack 顯示前一日結算尚未完成 (`prev_settlement` 缺失) 時拋出。"""


@dataclass
class DataPackState:
    """解析後的官方每日資料包狀態（「過去的分配與今日起點」）。"""

    team_id: str
    trade_date: str
    as_of_date: str | None
    generated_at: str
    is_day1: bool  # True 代表首日建倉（0 持股、10 億全現金）
    nav_tradable: float
    cash: float
    cash_ratio: float
    prev_holdings: dict[str, int] = field(default_factory=dict)  # {ticker: shares} (僅收錄 shares > 0)
    holding_prices: dict[str, float] = field(default_factory=dict)  # {ticker: close_price}
    holding_weights: dict[str, float] = field(default_factory=dict)  # {ticker: tradable_weight}
    overweight_tickers: list[str] = field(default_factory=list)  # 自然上漲超過單一權重上限之個股


@dataclass
class QuoteRecord:
    """單一股票之前一交易日收盤價量紀錄（供 M3 產生客觀 Observation 及 M5 計算股數）。"""

    ticker: str
    close_price: float
    chg_pct: float = 0.0  # 單日漲跌幅 (%)，如 +2.5 代表 +2.5%
    vol_lots: int = 1000  # 成交張數
    vol_ratio: float = 1.0  # 相對 20 日均量比
    content_as_of: str = "2026-05-03T14:30:00+08:00"
    url: str = ""
    authority: str = ""

    def __post_init__(self) -> None:
        if not self.authority and is_valid_ticker(self.ticker):
            self.authority = get_authority(self.ticker)
        if not self.url:
            if self.authority == "tpex":
                self.url = "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/pricing.html"
            else:
                self.url = "https://www.twse.com.tw/zh/trading/historical/mi-index.html"


@dataclass
class MacroRecord:
    """總體經濟與夜盤指標紀錄（固定成為 O1..Om 供 market_view.basis_refs 引用）。"""

    symbol: str  # 例如 "TWII", "SOX", "TSM_ADR"
    name: str  # 例如 "加權股價指數", "費城半導體指數"
    close: float
    chg_pct: float
    content_as_of: str
    authority: str = "twse"
    url: str = "https://www.twse.com.tw/zh/trading/historical/mi-index.html"


@dataclass
class ExtraSignalCard:
    """隊友或課堂 Lab 產出之外掛訊號卡（月營收、財報、新聞事件或跨股傳導）。"""

    ticker: str
    authority: str  # "mops", "fininst", "media", "vendor", "twse", "tpex"
    url: str
    content_as_of: str
    topic: str  # 如 "monthly_revenue", "supply_chain_event"
    statement: str  # 客觀事實陳述（5~500 字，不含主觀買賣建議）
    values: dict[str, float]
    polarity: str = "positive"  # "positive" 或 "negative"
    linked_target_ticker: str | None = None  # 若為 A->B 跨股事件，可指定受影響之下游標的 B


def parse_datapack(source: str | Path | dict[str, Any]) -> DataPackState:
    """解析官方 `DataPack_<team_id>_<trade_date>.json` 並執行未結算防呆檢查。"""
    if isinstance(source, (str, Path)):
        raw: dict[str, Any] = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        raw = dict(source)

    missing = raw.get("missing") or []
    status = raw.get("status", "ok")
    account = raw.get("account")
    holdings_list = raw.get("holdings")

    if "prev_settlement" in missing or status == "error" or account is None or holdings_list is None:
        raise SettlementNotReadyError(
            f"官方 DataPack 前一日結算尚未完成 (status={status!r}, missing={missing})！"
            "請等待官方伺服器完成前日結算後重新下載 DataPack，切勿以空倉盲目下單。"
        )

    team_id = str(raw.get("team_id", "Team_32"))
    trade_date = str(raw.get("trade_date", "2026-05-04"))
    as_of_date = raw.get("as_of_date")
    generated_at = str(raw.get("generated_at", f"{trade_date}T06:00:00+08:00"))

    nav_tradable = float(account.get("nav_tradable", 1_000_000_000.0))
    cash = float(account.get("cash", nav_tradable))
    cash_ratio = cash / nav_tradable if nav_tradable > 0 else 1.0

    prev_holdings: dict[str, int] = {}
    holding_prices: dict[str, float] = {}
    holding_weights: dict[str, float] = {}
    overweight_tickers: list[str] = []

    for item in holdings_list:
        code = str(item.get("code", ""))
        shares = int(item.get("shares", 0))
        c_price = float(item.get("close_price", 0.0))
        t_weight = float(item.get("tradable_weight", 0.0))
        if shares > 0:
            prev_holdings[code] = shares
            holding_prices[code] = c_price
            holding_weights[code] = t_weight
            if is_valid_ticker(code) and t_weight > get_max_weight(code) + 1e-6:
                overweight_tickers.append(code)

    is_day1 = len(prev_holdings) == 0
    return DataPackState(
        team_id=team_id,
        trade_date=trade_date,
        as_of_date=as_of_date,
        generated_at=generated_at,
        is_day1=is_day1,
        nav_tradable=nav_tradable,
        cash=cash,
        cash_ratio=round(cash_ratio, 6),
        prev_holdings=prev_holdings,
        holding_prices=holding_prices,
        holding_weights=holding_weights,
        overweight_tickers=overweight_tickers,
    )


def merge_quotes_with_datapack(
    dp_state: DataPackState,
    external_quotes: dict[str, QuoteRecord] | None = None,
    default_as_of: str | None = None,
) -> dict[str, QuoteRecord]:
    """將 DataPack 內的持倉收盤價與外部 150 檔收盤行情合併，確保每檔股票皆有收盤價。

    注意：對於既有持倉，強制優先採用官方 DataPack 的 `close_price`，以確保與伺服器委託驗算 100% 一致！
    """
    as_of_ts = default_as_of or (
        f"{dp_state.as_of_date}T14:30:00+08:00"
        if dp_state.as_of_date
        else dp_state.generated_at
    )
    merged: dict[str, QuoteRecord] = {}

    if external_quotes:
        for t, q in external_quotes.items():
            if is_valid_ticker(t):
                merged[t] = q

    # 強制以官方 DataPack 持倉的 close_price 覆蓋既有持股價格
    for code, price in dp_state.holding_prices.items():
        if code in merged:
            existing = merged[code]
            merged[code] = QuoteRecord(
                ticker=code,
                close_price=price,
                chg_pct=existing.chg_pct,
                vol_lots=existing.vol_lots,
                vol_ratio=existing.vol_ratio,
                content_as_of=existing.content_as_of,
                url=existing.url,
                authority=existing.authority,
            )
        elif is_valid_ticker(code):
            merged[code] = QuoteRecord(
                ticker=code,
                close_price=price,
                chg_pct=0.0,
                vol_lots=1000,
                vol_ratio=1.0,
                content_as_of=as_of_ts,
            )

    # 若在離線測試或外部 API 缺漏時，為剩餘白名單提供安全預設報價以防崩潰
    for code in UNIVERSE_150:
        if code not in merged:
            fallback_price = 1000.0 if code == "2330" else 100.0
            merged[code] = QuoteRecord(
                ticker=code,
                close_price=fallback_price,
                chg_pct=0.0,
                vol_lots=1000,
                vol_ratio=1.0,
                content_as_of=as_of_ts,
            )

    return merged


# ===========================================================================
# 子模組 2.2 ~ 2.5：外部真實行情抓取 (TWSE / TPEx OpenAPI + Yahoo 備援 + 快照)
# ===========================================================================
_TAIPEI_TZ = timezone(timedelta(hours=8))
_TWSE_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
_TPEX_DAY_ALL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
_TWSE_FMTQIK_URL = "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK"


def _roc_date_to_iso(roc_str: str, fallback_iso: str) -> str:
    """將民國年字串如 '1151026' 或 '115/10/26' 轉為台北時間 ISO-8601 收盤戳記。"""
    s = (roc_str or "").strip().replace("/", "").replace("-", "")
    if len(s) == 7 and s.isdigit():
        year = int(s[:3]) + 1911
        month = int(s[3:5])
        day = int(s[5:7])
        return f"{year:04d}-{month:02d}-{day:02d}T14:30:00+08:00"
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}T14:30:00+08:00"
    return fallback_iso


def _parse_float(val: Any) -> float | None:
    if val is None:
        return None
    s = str(val).strip().replace(",", "").replace("+", "").replace("X", "")
    if not s or s in ("--", "---", "N/A", "除權息", "除息", "除權"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _http_get_json(url: str, timeout: float = 12.0) -> Any:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EtfAgent/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw_bytes = resp.read()
        return json.loads(raw_bytes.decode("utf-8", errors="replace"))


def _fetch_yahoo_chart_quote(symbol: str, timeout: float = 8.0) -> tuple[float, float, int, str] | None:
    """從 Yahoo Finance v8 Chart API 取得 (close_price, chg_pct, vol_lots, content_as_of)。"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    try:
        data = _http_get_json(url, timeout=timeout)
        result = (data.get("chart") or {}).get("result")
        if not result:
            return None
        meta = result[0].get("meta") or {}
        indicators = (result[0].get("indicators") or {}).get("quote") or [{}]
        closes = [c for c in (indicators[0].get("close") or []) if c is not None]
        vols = [v for v in (indicators[0].get("volume") or []) if v is not None]
        timestamps = result[0].get("timestamp") or []
        if not closes:
            reg_price = _parse_float(meta.get("regularMarketPrice"))
            prev_close = _parse_float(meta.get("chartPreviousClose"))
            if not reg_price or reg_price <= 0:
                return None
            chg_pct = ((reg_price - prev_close) / prev_close * 100.0) if (prev_close and prev_close > 0) else 0.0
            ts_epoch = meta.get("regularMarketTime")
            as_of = (
                datetime.fromtimestamp(int(ts_epoch), tz=_TAIPEI_TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
                if ts_epoch
                else datetime.now(_TAIPEI_TZ).strftime("%Y-%m-%dT14:30:00+08:00")
            )
            return round(reg_price, 2), round(chg_pct, 2), 1000, as_of

        close_price = float(closes[-1])
        prev_close = float(closes[-2]) if len(closes) >= 2 else (_parse_float(meta.get("chartPreviousClose")) or close_price)
        chg_pct = ((close_price - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
        vol_shares = int(vols[-1]) if vols else 1_000_000
        vol_lots = max(1, vol_shares // 1000)
        if timestamps:
            dt_tw = datetime.fromtimestamp(int(timestamps[-1]), tz=_TAIPEI_TZ)
            as_of = dt_tw.strftime("%Y-%m-%dT14:30:00+08:00")
        else:
            as_of = datetime.now(_TAIPEI_TZ).strftime("%Y-%m-%dT14:30:00+08:00")
        return round(close_price, 2), round(chg_pct, 2), vol_lots, as_of
    except Exception:
        return None


def fetch_market_quotes(
    fallback_as_of: str | None = None,
    snapshot_dir: str | Path | None = None,
    override_csv: str | Path | None = None,
) -> dict[str, QuoteRecord]:
    """抓取 150 檔上市櫃個股最新收盤價（TWSE + TPEx OpenAPI 優先，Yahoo 與本機 CSV 備援）。"""
    default_ts = fallback_as_of or datetime.now(_TAIPEI_TZ).strftime("%Y-%m-%dT14:30:00+08:00")
    quotes: dict[str, QuoteRecord] = {}
    snap_path = Path(snapshot_dir) if snapshot_dir else None
    if snap_path:
        snap_path.mkdir(parents=True, exist_ok=True)

    # 1. 抓取 TWSE OpenAPI (上市 100 檔)
    try:
        twse_rows = _http_get_json(_TWSE_DAY_ALL_URL)
        if snap_path and isinstance(twse_rows, list):
            (snap_path / "twse_STOCK_DAY_ALL.json").write_text(
                json.dumps(twse_rows, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if isinstance(twse_rows, list):
            for row in twse_rows:
                code = str(row.get("Code", "")).strip()
                if code not in UNIVERSE_150:
                    continue
                c_price = _parse_float(row.get("ClosingPrice"))
                if not c_price or c_price <= 0:
                    continue
                chg_val = _parse_float(row.get("Change")) or 0.0
                prev_p = c_price - chg_val
                chg_pct = round((chg_val / prev_p) * 100.0, 2) if prev_p > 0 else 0.0
                vol_shares = _parse_float(row.get("TradeVolume")) or 1_000_000.0
                vol_lots = max(1, int(vol_shares // 1000))
                vol_ratio = round(min(3.5, max(0.5, vol_lots / 3000.0)), 2)
                as_of = _roc_date_to_iso(str(row.get("Date", "")), default_ts)
                quotes[code] = QuoteRecord(
                    ticker=code,
                    close_price=c_price,
                    chg_pct=chg_pct,
                    vol_lots=vol_lots,
                    vol_ratio=vol_ratio,
                    content_as_of=as_of,
                    url=_TWSE_DAY_ALL_URL,
                    authority="twse",
                )
    except Exception as exc:
        print(f"[WARN] TWSE OpenAPI fetch failed: {exc}")

    # 2. 抓取 TPEx OpenAPI (上櫃 50 檔)
    try:
        tpex_rows = _http_get_json(_TPEX_DAY_ALL_URL)
        if snap_path and isinstance(tpex_rows, list):
            (snap_path / "tpex_daily_close_quotes.json").write_text(
                json.dumps(tpex_rows, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if isinstance(tpex_rows, list):
            for row in tpex_rows:
                code = str(row.get("SecuritiesCompanyCode", "")).strip()
                if code not in UNIVERSE_150:
                    continue
                c_price = _parse_float(row.get("Close"))
                if not c_price or c_price <= 0:
                    continue
                chg_val = _parse_float(row.get("Change")) or 0.0
                prev_p = c_price - chg_val
                chg_pct = round((chg_val / prev_p) * 100.0, 2) if prev_p > 0 else 0.0
                vol_shares = _parse_float(row.get("TradingShares")) or 500_000.0
                vol_lots = max(1, int(vol_shares // 1000))
                vol_ratio = round(min(3.5, max(0.5, vol_lots / 1500.0)), 2)
                as_of = _roc_date_to_iso(str(row.get("Date", "")), default_ts)
                quotes[code] = QuoteRecord(
                    ticker=code,
                    close_price=c_price,
                    chg_pct=chg_pct,
                    vol_lots=vol_lots,
                    vol_ratio=vol_ratio,
                    content_as_of=as_of,
                    url=_TPEX_DAY_ALL_URL,
                    authority="tpex",
                )
    except Exception as exc:
        print(f"[WARN] TPEx OpenAPI fetch failed: {exc}")

    # 3. 針對仍缺漏的白名單個股，自動使用 Yahoo Finance Chart API 補齊
    missing_codes = [c for c in UNIVERSE_150 if c not in quotes]
    if missing_codes and len(missing_codes) <= 60:
        for code in missing_codes:
            auth = get_authority(code)
            yf_sym = f"{code}.TWO" if auth == "tpex" else f"{code}.TW"
            res = _fetch_yahoo_chart_quote(yf_sym)
            if res:
                c_price, chg_pct, vol_lots, as_of = res
                quotes[code] = QuoteRecord(
                    ticker=code,
                    close_price=c_price,
                    chg_pct=chg_pct,
                    vol_lots=vol_lots,
                    vol_ratio=1.2,
                    content_as_of=as_of,
                    url=f"https://finance.yahoo.com/quote/{yf_sym}",
                    authority="vendor",
                )

    # 4. 本機 CSV 覆蓋檔 (若存在 inputs/prices_override.csv)
    csv_path = Path(override_csv) if override_csv else Path("inputs/prices_override.csv")
    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = str(row.get("ticker") or row.get("code") or "").strip()
                    c_price = _parse_float(row.get("close_price") or row.get("close"))
                    if code in UNIVERSE_150 and c_price and c_price > 0:
                        chg_pct = _parse_float(row.get("chg_pct")) or 0.0
                        quotes[code] = QuoteRecord(
                            ticker=code,
                            close_price=c_price,
                            chg_pct=chg_pct,
                            vol_lots=int(_parse_float(row.get("vol_lots")) or 1000),
                            vol_ratio=_parse_float(row.get("vol_ratio")) or 1.0,
                            content_as_of=default_ts,
                        )
        except Exception as exc:
            print(f"[WARN] override_csv load failed: {exc}")

    return quotes


def fetch_macro_indicators(
    fallback_as_of: str | None = None,
    snapshot_dir: str | Path | None = None,
) -> list[MacroRecord]:
    """抓取加權指數 (TWII)、費城半導體 (SOX) 與台積電 ADR (TSM) 供 market_view.basis_refs 引用。"""
    default_ts = fallback_as_of or datetime.now(_TAIPEI_TZ).strftime("%Y-%m-%dT14:30:00+08:00")
    macros: list[MacroRecord] = []
    snap_path = Path(snapshot_dir) if snapshot_dir else None

    # 1. 台股加權指數 (優先從 TWSE FMTQIK OpenAPI 抓取)
    twii_added = False
    try:
        fmt_rows = _http_get_json(_TWSE_FMTQIK_URL)
        if snap_path and isinstance(fmt_rows, list):
            (snap_path / "twse_FMTQIK.json").write_text(
                json.dumps(fmt_rows, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if isinstance(fmt_rows, list) and fmt_rows:
            last = fmt_rows[-1]
            taiex = _parse_float(last.get("TAIEX"))
            chg = _parse_float(last.get("Change")) or 0.0
            if taiex and taiex > 0:
                prev_idx = taiex - chg
                chg_pct = round((chg / prev_idx) * 100.0, 2) if prev_idx > 0 else 0.0
                as_of = _roc_date_to_iso(str(last.get("Date", "")), default_ts)
                macros.append(
                    MacroRecord(
                        symbol="TWII",
                        name="臺灣加權股價指數",
                        close=taiex,
                        chg_pct=chg_pct,
                        content_as_of=as_of,
                        authority="twse",
                        url=_TWSE_FMTQIK_URL,
                    )
                )
                twii_added = True
    except Exception:
        pass

    if not twii_added:
        res = _fetch_yahoo_chart_quote("%5ETWII")
        if res:
            c_price, chg_pct, _, as_of = res
            macros.append(
                MacroRecord(
                    symbol="TWII",
                    name="臺灣加權股價指數",
                    close=c_price,
                    chg_pct=chg_pct,
                    content_as_of=as_of,
                    authority="vendor",
                    url="https://finance.yahoo.com/quote/%5ETWII",
                )
            )

    # 2. 費城半導體指數 (^SOX) 與 台積電 ADR (TSM)
    for yf_sym, sym_code, name in [
        ("%5ESOX", "SOX", "費城半導體指數"),
        ("TSM", "TSM_ADR", "台積電ADR"),
    ]:
        res = _fetch_yahoo_chart_quote(yf_sym)
        if res:
            c_price, chg_pct, _, as_of = res
            macros.append(
                MacroRecord(
                    symbol=sym_code,
                    name=name,
                    close=c_price,
                    chg_pct=chg_pct,
                    content_as_of=as_of,
                    authority="vendor",
                    url=f"https://finance.yahoo.com/quote/{yf_sym}",
                )
            )

    return macros


def load_extra_signals(signal_dir: str | Path = "inputs/extra_signals") -> list[ExtraSignalCard]:
    """讀取本機 `inputs/extra_signals/*.json` 隊友或課堂 Lab 外掛訊號卡。"""
    p_dir = Path(signal_dir)
    if not p_dir.exists():
        return []
    cards: list[ExtraSignalCard] = []
    for jf in sorted(p_dir.glob("*.json")):
        try:
            raw = json.loads(jf.read_text(encoding="utf-8"))
            items = raw if isinstance(raw, list) else [raw]
            for item in items:
                ticker = str(item.get("ticker", "")).strip()
                if not is_valid_ticker(ticker):
                    continue
                cards.append(
                    ExtraSignalCard(
                        ticker=ticker,
                        authority=str(item.get("authority", "mops")),
                        url=str(item.get("url", "https://mops.twse.com.tw/mops/web/t05st10_ifrs")),
                        content_as_of=str(item.get("content_as_of", "2026-10-26T18:00:00+08:00")),
                        topic=str(item.get("topic", "fundamental_signal")),
                        statement=str(item.get("statement", "")),
                        values={str(k): float(v) for k, v in (item.get("values") or {}).items()},
                        polarity=str(item.get("polarity", "positive")),
                        linked_target_ticker=item.get("linked_target_ticker"),
                    )
                )
        except Exception as exc:
            print(f"[WARN] Failed to load extra signal {jf}: {exc}")
    return cards

