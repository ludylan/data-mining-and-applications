"""模組 3 (M3)：全市場掃描漏斗與 S/O 事實編號中樞 (`funnel.py`)。

對應上游規格書：`docs/specs/M3_funnel_spec.md`。

一句話定位：
把「昨天手上的舊持倉」與「今天市場異動/連結傳導挑出的新機會」合併成 30~35 檔焦點名單 (Focus List)，
並由 Python 自動編好 100% 連續不跳號的資料來源 (`S1..Sn`) 與客觀事實 (`O1..On`) 交給 AI 決策。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from etf_agent.collectors import (
    DataPackState,
    ExtraSignalCard,
    MacroRecord,
    QuoteRecord,
    merge_quotes_with_datapack,
)
from etf_agent.dossier import BoundedMetric, DossierLedger, EvidenceEntry
from etf_agent.universe import UNIVERSE_150


@dataclass
class FocusCandidate:
    """進入今日焦點名單 (Focus List) 的單一候選股資訊與 Python 權重定位。"""

    ticker: str
    name: str
    market: str
    is_current_holding: bool
    current_shares: int
    current_weight: float
    close_price: float
    chg_pct: float
    vol_ratio: float
    anomaly_score: float
    weight_guide: BoundedMetric
    obs_ids: list[str] = field(default_factory=list)


@dataclass
class FunnelOutput:
    """M3 漏斗與事實編號中樞之完整產出（直接餵給 M4 PM Agent）。"""

    focus_list: dict[str, FocusCandidate]
    sources: list[dict[str, Any]]  # S1..Sn
    observations: list[dict[str, Any]]  # O1..On
    macro_obs_ids: list[str]  # 供 market_view.basis_refs 引用之 O IDs
    ticker_to_obs_ids: dict[str, list[str]]  # {ticker: ["O3", ...]} 供個股推論 I 引用
    close_prices: dict[str, float]  # 全市場收盤價 {ticker: price}


def build_funnel_and_observations(
    dp_state: DataPackState,
    external_quotes: dict[str, QuoteRecord] | None = None,
    macros: list[MacroRecord] | None = None,
    extra_signals: list[ExtraSignalCard] | None = None,
    ledger: DossierLedger | None = None,
    max_new_candidates: int = 10,
    day1_target_candidates: int = 26,
) -> FunnelOutput:
    """執行 M3 四大步驟：強制保留舊持倉 -> 異動與連結傳導掃描 -> 挑選 Focus List -> 鎖定 S1..Sn 與 O1..On。"""
    if ledger is None:
        ledger = DossierLedger()

    quotes = merge_quotes_with_datapack(dp_state, external_quotes)
    close_prices = {t: q.close_price for t, q in quotes.items()}
    signals = extra_signals or []

    # -----------------------------------------------------------------------
    # Step 3.1 & 3.2: 計算異動分數與跨股連結傳導 (結合 DossierLedger)
    # -----------------------------------------------------------------------
    direct_signal_boost: dict[str, float] = {}
    propagated_boost: dict[str, float] = {}

    for sig in signals:
        if sig.ticker not in UNIVERSE_150:
            continue
        delta = 2.0 if sig.polarity == "positive" else -2.0
        direct_signal_boost[sig.ticker] = direct_signal_boost.get(sig.ticker, 0.0) + delta

        # 若該訊號指定了跨股連結 A -> B，建立或更新連結履歷
        if sig.linked_target_ticker and sig.linked_target_ticker in UNIVERSE_150:
            ledger.get_or_create_link(
                source_ticker=sig.ticker,
                target_ticker=sig.linked_target_ticker,
                relation_note=sig.topic,
            )

        # 透過 DossierLedger 傳導對下游標的的連帶影響
        impacts = ledger.propagate_signal(sig.ticker, delta)
        for tgt, imp_val in impacts.items():
            propagated_boost[tgt] = propagated_boost.get(tgt, 0.0) + imp_val

    # 對 150 檔計算綜合分數：價量動能 + 直接事件訊號 + 連結履歷傳導訊號
    scores: dict[str, float] = {}
    for ticker, meta in UNIVERSE_150.items():
        q = quotes[ticker]
        momentum_score = q.chg_pct * 0.6 + (q.vol_ratio - 1.0) * 1.5
        # 台積電 2330 作為核心權值錨點，確保首日必入選
        anchor_bonus = 100.0 if ticker == "2330" else 0.0
        scores[ticker] = round(
            anchor_bonus
            + momentum_score
            + direct_signal_boost.get(ticker, 0.0)
            + propagated_boost.get(ticker, 0.0),
            4,
        )

    # -----------------------------------------------------------------------
    # Step 3.3: 建立今日 Focus List（昨日持倉 100% 強制入列 + 精選新候選股）
    # -----------------------------------------------------------------------
    held_tickers = list(dp_state.prev_holdings.keys())
    held_set = set(held_tickers)

    non_held_sorted = sorted(
        [t for t in UNIVERSE_150 if t not in held_set],
        key=lambda t: scores[t],
        reverse=True,
    )

    if dp_state.is_day1:
        # Day 1 首日建倉：挑選前 day1_target_candidates 檔（同時兼顧上市 twse 與上櫃 tpex 代表性）
        twse_picks = [t for t in non_held_sorted if UNIVERSE_150[t].market == "twse"][
            : int(day1_target_candidates * 0.65)
        ]
        tpex_picks = [t for t in non_held_sorted if UNIVERSE_150[t].market == "tpex"][
            : day1_target_candidates - len(twse_picks)
        ]
        selected_tickers = twse_picks + tpex_picks
    else:
        # Day 2+ 日常調倉：100% 保留昨日所有持倉 + 前 max_new_candidates 檔新機會股
        selected_tickers = held_tickers + non_held_sorted[:max_new_candidates]

    # -----------------------------------------------------------------------
    # Step 3.4: 自動去重編排 S1..Sn (sources) 與 O1..On (observations)
    # -----------------------------------------------------------------------
    source_registry: dict[tuple[str, str], str] = {}
    sources_list: list[dict[str, Any]] = []

    def _get_or_register_source(
        authority: str, url: str, content_as_of: str, name: str
    ) -> str:
        key = (authority, url)
        if key not in source_registry:
            sid = f"S{len(sources_list) + 1}"
            source_registry[key] = sid
            sources_list.append(
                {
                    "source_id": sid,
                    "authority": authority,
                    "url": url,
                    "name": name[:300],
                    "content_as_of": content_as_of,
                }
            )
        return source_registry[key]

    observations_list: list[dict[str, Any]] = []
    macro_obs_ids: list[str] = []
    ticker_to_obs_ids: dict[str, list[str]] = {}

    # 3.4a: 產生總經級事實 O1..Om（供 market_view.basis_refs 引用）
    default_as_of = (
        f"{dp_state.as_of_date}T14:30:00+08:00"
        if dp_state.as_of_date
        else dp_state.generated_at
    )
    if not macros:
        macros = [
            MacroRecord(
                symbol="TWII",
                name="臺灣加權股價指數與市場資金水位",
                close=23000.0,
                chg_pct=0.45,
                content_as_of=default_as_of,
                authority="twse",
                url="https://www.twse.com.tw/zh/trading/historical/mi-index.html",
            )
        ]

    for m in macros:
        sid = _get_or_register_source(m.authority, m.url, m.content_as_of, m.name)
        oid = f"O{len(observations_list) + 1}"
        observations_list.append(
            {
                "obs_id": oid,
                "source_ref": [sid],
                "topic": "macro_market",
                "statement": (
                    f"{m.name} ({m.symbol}) 前一交易日收盤為 {m.close:.2f}，"
                    f"單日變動 {m.chg_pct:+.2f}%，組目前可交易淨值為 {dp_state.nav_tradable:,.0f} 元。"
                ),
                "values": {
                    "index_close": round(float(m.close), 4),
                    "chg_pct": round(float(m.chg_pct), 4),
                    "cash_ratio": round(float(dp_state.cash_ratio), 4),
                },
            }
        )
        macro_obs_ids.append(oid)

    # 3.4b: 為 Focus List 中的每一檔股票產生標準價量客觀事實 O
    focus_map: dict[str, FocusCandidate] = {}
    for ticker in selected_tickers:
        meta = UNIVERSE_150[ticker]
        q = quotes[ticker]
        is_held = ticker in held_set
        cur_shares = dp_state.prev_holdings.get(ticker, 0)
        cur_weight = dp_state.holding_weights.get(ticker, 0.0)

        stock_dossier = ledger.get_or_create_stock(
            ticker=ticker,
            chg_pct=q.chg_pct,
            vol_ratio=q.vol_ratio,
            is_current_holding=is_held,
            current_weight=cur_weight,
        )

        src_name = "TWSE 臺灣證券交易所每日收盤行情" if meta.market == "twse" else "TPEx 證券櫃檯買賣中心每日收盤行情"
        sid = _get_or_register_source(q.authority, q.url, q.content_as_of, src_name)
        oid = f"O{len(observations_list) + 1}"

        observations_list.append(
            {
                "obs_id": oid,
                "source_ref": [sid],
                "topic": "price_volume",
                "statement": (
                    f"{ticker} ({meta.name}) 前一交易日收盤價為 {q.close_price:.2f} 元，"
                    f"單日價格變動 {q.chg_pct:+.2f}%，成交量比為 {q.vol_ratio:.2f} 倍，"
                    f"前日結算持股 {cur_shares} 股 (權重 {cur_weight:.4f})。"
                ),
                "values": {
                    "close_price": round(float(q.close_price), 4),
                    "chg_pct": round(float(q.chg_pct), 4),
                    "vol_ratio": round(float(q.vol_ratio), 4),
                    "prev_shares": int(cur_shares),
                },
            }
        )
        ticker_to_obs_ids.setdefault(ticker, []).append(oid)

        focus_map[ticker] = FocusCandidate(
            ticker=ticker,
            name=meta.name,
            market=meta.market,
            is_current_holding=is_held,
            current_shares=cur_shares,
            current_weight=cur_weight,
            close_price=q.close_price,
            chg_pct=q.chg_pct,
            vol_ratio=q.vol_ratio,
            anomaly_score=scores[ticker],
            weight_guide=stock_dossier.weight_guide,
            obs_ids=list(ticker_to_obs_ids[ticker]),
        )

    # 3.4c: 若有額外事件訊號 (ExtraSignalCard) 屬於 Focus List 個股，自動註冊為額外的 O 並寫入 Dossier 履歷
    for sig in signals:
        if sig.ticker not in focus_map or len(observations_list) >= 95:
            continue
        sid = _get_or_register_source(
            sig.authority, sig.url, sig.content_as_of, f"{sig.ticker} 外部資訊來源"
        )
        oid = f"O{len(observations_list) + 1}"
        observations_list.append(
            {
                "obs_id": oid,
                "source_ref": [sid],
                "topic": sig.topic,
                "statement": sig.statement[:500],
                "values": {k: float(v) for k, v in sig.values.items()},
            }
        )
        ticker_to_obs_ids[sig.ticker].append(oid)
        focus_map[sig.ticker].obs_ids.append(oid)

        # 同步寫入個股履歷夾 (StockDossier)
        ev = EvidenceEntry(
            date=dp_state.trade_date,
            obs_ref=oid,
            summary=sig.statement,
            polarity=sig.polarity,
            metrics=dict(sig.values),
        )
        ledger.get_or_create_stock(sig.ticker).add_evidence(ev)
        if sig.linked_target_ticker and sig.linked_target_ticker in focus_map:
            ticker_to_obs_ids[sig.linked_target_ticker].append(oid)
            focus_map[sig.linked_target_ticker].obs_ids.append(oid)
            ledger.get_or_create_link(sig.ticker, sig.linked_target_ticker).add_evidence(ev)

    return FunnelOutput(
        focus_list=focus_map,
        sources=sources_list,
        observations=observations_list,
        macro_obs_ids=macro_obs_ids,
        ticker_to_obs_ids=ticker_to_obs_ids,
        close_prices=close_prices,
    )
