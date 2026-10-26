"""模組 5 (M5)：沙盒試算與合規守門員 (`sandbox.py`)。

對應上游規格書：`docs/specs/M5_sandbox_spec.md` 與 `docs/D-Plan.schema_v4.3.json`。

三大核心子模組：
1. 子模組 5.1 (`derive_orders_and_normalize`)：
   嚴格依照《D-Plan 撰寫指南》§⑥ 公式，由 `decisions` 之 `target_weight` 機械推導 `orders`（千股無條件捨去）。
   若換算後異動張數為 0（不足 1,000 股），自動將既有持股移回 `no_trade_decisions` 並重編 `D1..Dk` 流水號，杜絕 0 股委託單。
2. 子模組 5.2 (`verify_dplan`)：
   執行官方八大鐵律驗算（Schema v4.3 格式、S->O->I->D->Order 連續編號與引用鏈、昨日持股 100% 覆蓋率、
   20~30 檔限制、2330<=25%/其餘<=10% 權重上限、0~25% 稅費後現金水位、Posture 淨流向與 funding_for 缺口檢核、Active Share >= 15%）。
3. 子模組 5.3 (`align_posture_and_funding`)：
   針對千股取整造成的微幅算術偏差，自動校準 `market_view.posture` 現金區間、淨曝險方向，並自動拔除不符缺口條件的多餘 `funding_for` 標記。
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any

from etf_agent.universe import (
    UNIVERSE_150,
    compute_active_share,
    get_max_weight,
    is_valid_ticker,
)

# ---------------------------------------------------------------------------
# 官方競賽常數 (依據 docs/ai_cup_2026.md 與 D-Plan.schema_v4.3.json)
# ---------------------------------------------------------------------------
BUY_COMMISSION_RATE: float = 0.001425  # 買進手續費 0.1425%
SELL_COMMISSION_RATE: float = 0.001425  # 賣出手續費 0.1425%
SELL_TAX_RATE: float = 0.003  # 賣出證交稅 0.3%
HOLD_NET_FLOW_TOLERANCE: float = 0.02  # posture="hold" 容差：|買-賣| <= 2% NAV
MIN_HOLDINGS_COUNT: int = 20
MAX_HOLDINGS_COUNT: int = 30
MIN_CASH_RATIO: float = 0.0
MAX_CASH_RATIO: float = 0.25
MIN_ACTIVE_SHARE: float = 0.15
LOT_SIZE: int = 1000  # 台股整張單位：1,000 股

# Schema v4.3 正規表達式與列舉常數
_RE_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_RE_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?\+08:00$"
)
_RE_TEAM_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_RE_TICKER = re.compile(r"^[0-9]{4}$")
_RE_TOPIC = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_RE_CODE_VER = re.compile(r"^[A-Za-z0-9_.:/\-]{1,64}$")

_VALID_AUTHORITIES = {
    "twse",
    "tpex",
    "taifex",
    "mops",
    "fininst",
    "media",
    "vendor",
    "other",
}
_VALID_REGIMES = {"risk_on", "neutral", "risk_off"}
_VALID_STANCES = {"aggressive", "neutral", "defensive"}
_VALID_INTENTS = {"increase", "hold", "reduce"}
_VALID_ACTIONS = {"BUY", "ADD", "TRIM", "SELL_ALL"}
_VALID_MODEL_PROVIDERS = {
    "anthropic",
    "openai",
    "google",
    "xai",
    "deepseek",
    "alibaba",
    "meta",
    "mistral",
    "cohere",
    "amazon",
    "microsoft",
    "nvidia",
    "taide",
    "other",
}
_REQUIRED_TOP_KEYS = {
    "schema_version",
    "doc_type",
    "team_id",
    "trade_date",
    "sources",
    "observations",
    "market_view",
    "inferences",
    "decisions",
    "no_trade_decisions",
    "orders",
    "agent_metadata",
}
_ALLOWED_TOP_KEYS = _REQUIRED_TOP_KEYS | {"_NOTE"}


@dataclass
class VerificationReport:
    """八大鐵律驗算結果報告，供 M4 PM Agent 自我修正迴圈讀取。"""

    passed: bool
    errors: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def format_feedback_for_agent(self) -> str:
        """將違規清單轉為給 M4 PM Agent 的結構化『退回原因 + 思考補齊指引』。"""
        if self.passed:
            return "PASS: D-Plan 通過所有 8 項官方合規與數學一致性檢核。"
        lines = [
            f"FAIL: 共發現 {len(self.errors)} 項合規或數學一致性問題，系統退回重審。請根據以下原因重新思考並補齊缺漏："
        ]
        for idx, err in enumerate(self.errors, start=1):
            lines.append(f"  {idx}. 【退回原因】{err}")
            if "持倉覆蓋" in err:
                lines.append("     👉【補齊指引】請檢查昨日所有 shares > 0 的持股，將未異動的持股補入 no_trade_decisions 並附上對應推論 I。")
            elif "持股檔數" in err:
                lines.append("     👉【補齊指引】請在 Focus List 中增減 BUY 或 SELL_ALL 決策，使成交後總持股檔數嚴格落在 20~30 檔。")
            elif "權重上限" in err:
                lines.append("     👉【補齊指引】請將超標個股之 target_weight 下調至硬上限以內（2330 <= 0.25，其餘 <= 0.10）。")
            elif "現金" in err or "Posture" in err or "資金調度" in err:
                lines.append("     👉【補齊指引】請重新平衡買賣超權重使稅後現金比落在 0%~25% 及 target_cash_pct_range 內，或呼叫 align_posture_and_funding 對齊。")
            elif "引用鏈" in err:
                lines.append("     👉【補齊指引】請確保所有 S/O/I/D 從 1 連續編號不跳號，且只引用真實存在的 ID。")
        if self.metrics:
            lines.append(
                f"當前試算摘要：預計持股檔數={self.metrics.get('post_trade_holdings_count')}, "
                f"預估現金比={self.metrics.get('estimated_cash_ratio', 0.0):.4f}, "
                f"買進總額={self.metrics.get('buy_amount', 0.0):,.0f}, "
                f"賣出總額={self.metrics.get('sell_amount', 0.0):,.0f}"
            )
        return "\n".join(lines)


# ===========================================================================
# 子模組 5.1：官方委託公式推導器 (derive_orders_and_normalize)
# ===========================================================================
def compute_target_shares(
    target_weight: float,
    nav_tradable: float,
    close_price: float,
    action: str = "BUY",
) -> int:
    """嚴格依《D-Plan 撰寫指南》§⑥ 公式計算目標股數（千股無條件捨去）。

    target_shares = floor((target_weight * nav_tradable) / (close_price * 1000)) * 1000
    """
    if action == "SELL_ALL" or target_weight <= 0.0:
        return 0
    if close_price <= 0.0 or nav_tradable <= 0.0:
        raise ValueError(f"無效的收盤價 ({close_price}) 或可交易淨值 ({nav_tradable})")
    lots = math.floor((target_weight * nav_tradable) / (close_price * LOT_SIZE))
    return max(0, int(lots * LOT_SIZE))


def derive_orders_and_normalize(
    dplan: dict[str, Any],
    nav_tradable: float,
    prev_holdings: dict[str, int],
    close_prices: dict[str, float],
    in_place: bool = False,
) -> dict[str, Any]:
    """從 `decisions` 機械推導 `orders`，並自動處理 0 股委託移回 `no_trade_decisions` 與 `D1..Dk` 重編碼。

    參數:
        dplan: 待處理之 D-Plan 字典。
        nav_tradable: 前一日結算可交易淨值 (`account.nav_tradable`)。
        prev_holdings: 前一日結算持股字典 `{ticker: shares}`（僅 `shares > 0` 視為既有持倉）。
        close_prices: 前一日收盤價字典 `{ticker: close_price}`。
        in_place: 是否直接修改傳入的 `dplan`。預設 False 回傳深拷貝。
    """
    plan = dplan if in_place else copy.deepcopy(dplan)
    raw_decisions: list[dict[str, Any]] = list(plan.get("decisions", []))
    no_trades: list[dict[str, Any]] = list(plan.get("no_trade_decisions", []))
    existing_no_trade_tickers = {nt["ticker"] for nt in no_trades if "ticker" in nt}

    active_entries: list[tuple[str, dict[str, Any], int, float]] = []
    # 暫存 (old_decision_id, normalized_decision_dict, delta_shares, close_price)

    for dec in raw_decisions:
        ticker = dec.get("ticker", "")
        action = dec.get("action", "BUY")
        target_weight = float(dec.get("target_weight", 0.0))
        old_id = dec.get("decision_id", "")
        cur_shares = int(prev_holdings.get(ticker, 0))
        price = float(close_prices.get(ticker, 0.0))

        if price <= 0.0:
            raise ValueError(f"缺少股票 {ticker} 的有效前日收盤價 (close_price={price})")

        if action == "SELL_ALL":
            target_shares = 0
            target_weight = 0.0
        else:
            target_shares = compute_target_shares(target_weight, nav_tradable, price, action)

        delta_shares = target_shares - cur_shares

        # 不得超賣既有庫存
        if delta_shares < 0 and abs(delta_shares) > cur_shares:
            delta_shares = -cur_shares
            target_shares = 0

        # Case A: 換算後異動股數為 0 (不足 1 張 1,000 股)
        if delta_shares == 0:
            if cur_shares > 0 and ticker not in existing_no_trade_tickers:
                inf_refs = dec.get("inference_refs", ["I1"])
                no_trades.append(
                    {
                        "ticker": ticker,
                        "reason_refs": inf_refs[:5],
                        "reason": "目標權重經千股取整後異動張數為 0，維持原持倉續抱。",
                    }
                )
                existing_no_trade_tickers.add(ticker)
            continue

        # Case B: 有實際異動股數 (|delta_shares| >= 1000)，校正 action 語義與持倉狀態一致
        new_dec = copy.deepcopy(dec)
        new_dec["target_weight"] = round(target_weight, 6)
        if delta_shares > 0:
            new_dec["action"] = "BUY" if cur_shares == 0 else "ADD"
            # BUY/ADD 決策不可帶有 funding_for
            new_dec.pop("funding_for", None)
        else:
            if target_shares == 0:
                new_dec["action"] = "SELL_ALL"
                new_dec["target_weight"] = 0.0
            else:
                new_dec["action"] = "TRIM"

        active_entries.append((old_id, new_dec, delta_shares, price))

    # 重新連續編號 D1..Dk，並建立 old_id -> new_id 對照表
    old_to_new_id: dict[str, str] = {}
    buy_add_new_ids: set[str] = set()
    final_decisions: list[dict[str, Any]] = []
    final_orders: list[dict[str, Any]] = []

    for idx, (old_id, new_dec, delta_shares, price) in enumerate(active_entries, start=1):
        new_id = f"D{idx}"
        if old_id:
            old_to_new_id[old_id] = new_id
        new_dec["decision_id"] = new_id
        if new_dec["action"] in ("BUY", "ADD"):
            buy_add_new_ids.add(new_id)
        final_decisions.append(new_dec)

        cur_shares = int(prev_holdings.get(new_dec["ticker"], 0))
        tw = new_dec["target_weight"]
        final_orders.append(
            {
                "ticker": new_dec["ticker"],
                "side": "BUY" if delta_shares > 0 else "SELL",
                "shares": abs(delta_shares),
                "decision_ref": new_id,
                "derivation_detail": (
                    f"floor({tw:.4f}*{nav_tradable:.0f}/({price}*1000))*1000"
                    f" - {cur_shares} = {delta_shares:+d}"
                ),
            }
        )

    # 同步更新 TRIM / SELL_ALL 上的 funding_for 引用編號
    for dec in final_decisions:
        if "funding_for" in dec:
            mapped_refs = []
            for ref in dec["funding_for"]:
                mapped = old_to_new_id.get(ref, ref)
                if mapped in buy_add_new_ids and mapped not in mapped_refs:
                    mapped_refs.append(mapped)
            if mapped_refs:
                dec["funding_for"] = mapped_refs[:5]
            else:
                dec.pop("funding_for", None)

    plan["decisions"] = final_decisions
    plan["no_trade_decisions"] = no_trades
    plan["orders"] = final_orders
    return plan


# ===========================================================================
# 子模組 5.3：Posture 與 funding_for 數學自動對齊器 (align_posture_and_funding)
# ===========================================================================
def align_posture_and_funding(
    dplan: dict[str, Any],
    prev_cash: float,
    nav_tradable: float,
    close_prices: dict[str, float],
    cash_buffer: float = 0.03,
    in_place: bool = False,
) -> dict[str, Any]:
    """根據 `orders` 實算出的買賣金額與稅後現金比，自動對齊 `posture` 與清理無效 `funding_for`。

    解決千股取整後導致預估現金比微幅偏離 `target_cash_pct_range` 或「錢夠卻宣稱調度」的退件風險。
    """
    plan = dplan if in_place else copy.deepcopy(dplan)
    orders: list[dict[str, Any]] = plan.get("orders", [])
    decisions: list[dict[str, Any]] = plan.get("decisions", [])

    buy_amt = sum(
        o["shares"] * close_prices[o["ticker"]] for o in orders if o.get("side") == "BUY"
    )
    sell_amt = sum(
        o["shares"] * close_prices[o["ticker"]] for o in orders if o.get("side") == "SELL"
    )
    fees_and_tax = (
        buy_amt * BUY_COMMISSION_RATE
        + sell_amt * (SELL_COMMISSION_RATE + SELL_TAX_RATE)
    )
    est_cash = prev_cash - buy_amt + sell_amt - fees_and_tax
    est_cash_ratio = est_cash / nav_tradable if nav_tradable > 0 else 0.0

    mv = plan.get("market_view", {})
    posture = mv.get("posture", {})

    # 1. 對齊 net_exposure_intent
    current_intent = posture.get("net_exposure_intent", "hold")
    net_diff = buy_amt - sell_amt
    if abs(net_diff) <= HOLD_NET_FLOW_TOLERANCE * nav_tradable and current_intent == "hold":
        posture["net_exposure_intent"] = "hold"
    elif net_diff > 0:
        posture["net_exposure_intent"] = "increase"
    elif net_diff < 0:
        posture["net_exposure_intent"] = "reduce"
    else:
        posture["net_exposure_intent"] = "hold"

    # 2. 對齊 target_cash_pct_range (若實算現金比落在 [0, 0.25] 內，確保區間包覆實算值)
    if MIN_CASH_RATIO <= est_cash_ratio <= MAX_CASH_RATIO:
        rng = posture.get("target_cash_pct_range", [0.05, 0.15])
        if (
            not isinstance(rng, list)
            or len(rng) != 2
            or not (rng[0] <= est_cash_ratio <= rng[1])
            or rng[0] < MIN_CASH_RATIO
            or rng[1] > MAX_CASH_RATIO
        ):
            low = max(MIN_CASH_RATIO, round(est_cash_ratio - cash_buffer, 4))
            high = min(MAX_CASH_RATIO, round(est_cash_ratio + cash_buffer, 4))
            if low > high:
                low = high
            posture["target_cash_pct_range"] = [low, high]

    # 3. 檢核 funding_for 缺口存在性：若不執行調度賣出時現金比仍 >= 下限，則自動移除 funding_for
    target_low = posture.get("target_cash_pct_range", [0.05, 0.15])[0]
    funding_sell_ids = {
        d["decision_id"]
        for d in decisions
        if d.get("funding_for") and d.get("action") in ("TRIM", "SELL_ALL")
    }
    if funding_sell_ids:
        non_funding_sell_amt = sum(
            o["shares"] * close_prices[o["ticker"]]
            for o in orders
            if o.get("side") == "SELL" and o.get("decision_ref") not in funding_sell_ids
        )
        cost_without_funding = (
            buy_amt * BUY_COMMISSION_RATE
            + non_funding_sell_amt * (SELL_COMMISSION_RATE + SELL_TAX_RATE)
        )
        cash_without_funding = prev_cash - buy_amt + non_funding_sell_amt - cost_without_funding
        ratio_without_funding = (
            cash_without_funding / nav_tradable if nav_tradable > 0 else 0.0
        )
        if ratio_without_funding >= target_low:
            # 現金本來就夠支付買單並維持目標下限，移除多餘的 funding_for 宣告避免被退件
            for d in decisions:
                d.pop("funding_for", None)

    return plan


# ===========================================================================
# 子模組 5.2：八大鐵律與數學一致性驗算器 (verify_dplan)
# ===========================================================================
def verify_dplan(
    dplan: dict[str, Any],
    nav_tradable: float,
    prev_cash: float,
    prev_holdings: dict[str, int],
    close_prices: dict[str, float],
    etf_benchmarks: dict[str, dict[str, float]] | None = None,
) -> VerificationReport:
    """執行官方 D-Plan v4.3 八大鐵律驗算，回傳 `VerificationReport`。"""
    errors: list[str] = []

    # -----------------------------------------------------------------------
    # Check 1: JSON Schema v4.3 結構、型別、長度與正規表達式檢核
    # -----------------------------------------------------------------------
    _check_1_schema(dplan, errors)

    # 若頂層必填結構嚴重缺失，直接回傳避免後續 KeyError
    if not isinstance(dplan, dict) or not _REQUIRED_TOP_KEYS.issubset(dplan.keys()):
        return VerificationReport(passed=False, errors=errors)

    # -----------------------------------------------------------------------
    # Check 2: 連續編號 (S1..Sn, O1..On, I1..In, D1..Dk) 與完整引用鏈檢核
    # -----------------------------------------------------------------------
    _check_2_id_and_reference_chain(
        dplan=dplan,
        nav_tradable=nav_tradable,
        prev_holdings=prev_holdings,
        close_prices=close_prices,
        errors=errors,
    )

    # -----------------------------------------------------------------------
    # Check 3: 昨日持倉 (shares > 0) 100% 覆蓋率與互斥檢核
    # -----------------------------------------------------------------------
    _check_3_holdings_coverage(
        dplan=dplan,
        prev_holdings=prev_holdings,
        errors=errors,
    )

    # -----------------------------------------------------------------------
    # Check 4 & 5: 150 檔白名單、成交後 20~30 檔限制、單一權重上限與不超賣
    # -----------------------------------------------------------------------
    post_shares, post_weights = _check_4_and_5_universe_count_and_weights(
        dplan=dplan,
        nav_tradable=nav_tradable,
        prev_holdings=prev_holdings,
        close_prices=close_prices,
        errors=errors,
    )

    # -----------------------------------------------------------------------
    # Check 6 & 7: 稅費後現金水位 (0~25%)、Posture 淨流向與 funding_for 缺口檢核
    # -----------------------------------------------------------------------
    cash_metrics = _check_6_and_7_cash_posture_and_funding(
        dplan=dplan,
        nav_tradable=nav_tradable,
        prev_cash=prev_cash,
        close_prices=close_prices,
        errors=errors,
    )

    # -----------------------------------------------------------------------
    # Check 8: Active Share >= 15% (與 30 檔主動型 ETF 前 15 大持股比對)
    # -----------------------------------------------------------------------
    min_active_share: float | None = None
    if etf_benchmarks:
        min_active_share = _check_8_active_share(
            post_weights=post_weights,
            etf_benchmarks=etf_benchmarks,
            errors=errors,
        )

    active_count = sum(1 for sh in post_shares.values() if sh > 0)
    metrics: dict[str, Any] = {
        **cash_metrics,
        "post_trade_holdings_count": active_count,
        "post_trade_weights": post_weights,
        "min_active_share": min_active_share,
    }
    return VerificationReport(passed=(len(errors) == 0), errors=errors, metrics=metrics)


# ---------------------------------------------------------------------------
# 內部檢核實作函式
# ---------------------------------------------------------------------------
def _check_1_schema(dplan: Any, errors: list[str]) -> None:
    if not isinstance(dplan, dict):
        errors.append("[Check 1 Schema] D-Plan 根節點必須是 JSON object。")
        return

    missing = _REQUIRED_TOP_KEYS - set(dplan.keys())
    if missing:
        errors.append(f"[Check 1 Schema] 缺少頂層必填欄位：{sorted(missing)}")
        return

    extra = set(dplan.keys()) - _ALLOWED_TOP_KEYS
    if extra:
        errors.append(f"[Check 1 Schema] 頂層包含非法額外欄位 (additionalProperties: false)：{sorted(extra)}")

    if not re.match(r"^4\.\d+$", str(dplan.get("schema_version", ""))):
        errors.append(f"[Check 1 Schema] schema_version 格式錯誤：{dplan.get('schema_version')!r}")

    if dplan.get("doc_type") != "D-Plan":
        errors.append(f"[Check 1 Schema] doc_type 必須為 'D-Plan'，實得：{dplan.get('doc_type')!r}")

    if not _RE_TEAM_ID.match(str(dplan.get("team_id", ""))):
        errors.append(f"[Check 1 Schema] team_id 格式不合法：{dplan.get('team_id')!r}")

    if not _RE_DATE.match(str(dplan.get("trade_date", ""))):
        errors.append(f"[Check 1 Schema] trade_date 必須為 YYYY-MM-DD：{dplan.get('trade_date')!r}")

    # sources (1..50)
    sources = dplan.get("sources")
    if not isinstance(sources, list) or not (1 <= len(sources) <= 50):
        errors.append("[Check 1 Schema] sources 必須為長度 1~50 的陣列。")
    else:
        allowed_src_keys = {
            "source_id",
            "authority",
            "url",
            "name",
            "archive_url",
            "published_at",
            "content_as_of",
            "fetched_at",
        }
        for s in sources:
            if not isinstance(s, dict):
                errors.append("[Check 1 Schema] sources 元素必須為 object。")
                continue
            if set(s.keys()) - allowed_src_keys:
                errors.append(f"[Check 1 Schema] source {s.get('source_id')} 含未允許欄位。")
            if s.get("authority") not in _VALID_AUTHORITIES:
                errors.append(f"[Check 1 Schema] source {s.get('source_id')} authority 無效：{s.get('authority')!r}")
            url = str(s.get("url", ""))
            if not (url.startswith("http://") or url.startswith("https://")):
                errors.append(f"[Check 1 Schema] source {s.get('source_id')} url 必須為合法 URI：{url!r}")
            for ts_key in ("content_as_of", "published_at", "fetched_at"):
                if ts_key in s and not _RE_TIMESTAMP.match(str(s[ts_key])):
                    errors.append(
                        f"[Check 1 Schema] source {s.get('source_id')} {ts_key} 必須為 +08:00 ISO 時間：{s.get(ts_key)!r}"
                    )
            if "content_as_of" not in s:
                errors.append(f"[Check 1 Schema] source {s.get('source_id')} 缺少必填欄位 content_as_of。")

    # observations (1..100)
    observations = dplan.get("observations")
    if not isinstance(observations, list) or not (1 <= len(observations) <= 100):
        errors.append("[Check 1 Schema] observations 必須為長度 1~100 的陣列。")
    else:
        allowed_obs_keys = {"obs_id", "source_ref", "topic", "statement", "values"}
        for o in observations:
            if not isinstance(o, dict):
                errors.append("[Check 1 Schema] observations 元素必須為 object。")
                continue
            if set(o.keys()) - allowed_obs_keys:
                errors.append(f"[Check 1 Schema] observation {o.get('obs_id')} 含未允許欄位。")
            stmt = str(o.get("statement", ""))
            if not (5 <= len(stmt) <= 500):
                errors.append(
                    f"[Check 1 Schema] observation {o.get('obs_id')} statement 長度須介於 5~500 字 (目前 {len(stmt)} 字)。"
                )
            if "topic" in o and not _RE_TOPIC.match(str(o["topic"])):
                errors.append(f"[Check 1 Schema] observation {o.get('obs_id')} topic 格式錯誤：{o.get('topic')!r}")
            vals = o.get("values")
            if not isinstance(vals, dict) or len(vals) < 1:
                errors.append(f"[Check 1 Schema] observation {o.get('obs_id')} values 至少須有 1 個數值鍵值對。")
            else:
                for k, v in vals.items():
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        errors.append(
                            f"[Check 1 Schema] observation {o.get('obs_id')} values[{k!r}] 必須為數字，不可為 {type(v).__name__}。"
                        )

    # market_view
    mv = dplan.get("market_view")
    if not isinstance(mv, dict):
        errors.append("[Check 1 Schema] market_view 必須為 object。")
    else:
        req_mv = {"basis_refs", "logic", "regime", "stance", "posture", "counter_evidence"}
        allowed_mv = req_mv | {"confidence"}
        if not req_mv.issubset(mv.keys()):
            errors.append(f"[Check 1 Schema] market_view 缺少必填欄位：{sorted(req_mv - set(mv.keys()))}")
        if set(mv.keys()) - allowed_mv:
            errors.append(f"[Check 1 Schema] market_view 含未允許欄位：{sorted(set(mv.keys()) - allowed_mv)}")
        mv_logic = str(mv.get("logic", ""))
        if not (20 <= len(mv_logic) <= 1000):
            errors.append(f"[Check 1 Schema] market_view.logic 長度須介於 20~1000 字 (目前 {len(mv_logic)} 字)。")
        if mv.get("regime") not in _VALID_REGIMES:
            errors.append(f"[Check 1 Schema] market_view.regime 無效：{mv.get('regime')!r}")
        if mv.get("stance") not in _VALID_STANCES:
            errors.append(f"[Check 1 Schema] market_view.stance 無效：{mv.get('stance')!r}")
        posture = mv.get("posture")
        if not isinstance(posture, dict):
            errors.append("[Check 1 Schema] market_view.posture 必須為 object。")
        else:
            if posture.get("net_exposure_intent") not in _VALID_INTENTS:
                errors.append(
                    f"[Check 1 Schema] posture.net_exposure_intent 無效：{posture.get('net_exposure_intent')!r}"
                )
            rng = posture.get("target_cash_pct_range")
            if (
                not isinstance(rng, list)
                or len(rng) != 2
                or not all(isinstance(x, (int, float)) for x in rng)
                or rng[0] < 0.0
                or rng[1] > 0.25
                or rng[0] > rng[1]
            ):
                errors.append(
                    f"[Check 1 Schema] posture.target_cash_pct_range 必須為 [下限, 上限] 且介於 0~0.25：{rng!r}"
                )

    # inferences (1..60)
    inferences = dplan.get("inferences")
    if not isinstance(inferences, list) or not (1 <= len(inferences) <= 60):
        errors.append("[Check 1 Schema] inferences 必須為長度 1~60 的陣列。")
    else:
        req_inf = {"inf_id", "premise_refs", "logic", "counter_evidence"}
        allowed_inf = req_inf | {"conclusion", "confidence"}
        for inf in inferences:
            if not isinstance(inf, dict):
                errors.append("[Check 1 Schema] inferences 元素必須為 object。")
                continue
            if not req_inf.issubset(inf.keys()):
                errors.append(f"[Check 1 Schema] inference {inf.get('inf_id')} 缺少必填欄位。")
            if set(inf.keys()) - allowed_inf:
                errors.append(f"[Check 1 Schema] inference {inf.get('inf_id')} 含未允許欄位。")
            inf_logic = str(inf.get("logic", ""))
            if not (20 <= len(inf_logic) <= 1000):
                errors.append(
                    f"[Check 1 Schema] inference {inf.get('inf_id')} logic 長度須介於 20~1000 字 (目前 {len(inf_logic)} 字)。"
                )

    # decisions (0..60)
    decisions = dplan.get("decisions")
    if not isinstance(decisions, list) or len(decisions) > 60:
        errors.append("[Check 1 Schema] decisions 必須為長度 0~60 的陣列。")
    else:
        req_dec = {"decision_id", "ticker", "action", "target_weight", "inference_refs"}
        allowed_dec = req_dec | {"risk_check", "funding_for"}
        for d in decisions:
            if not isinstance(d, dict):
                errors.append("[Check 1 Schema] decisions 元素必須為 object。")
                continue
            if not req_dec.issubset(d.keys()):
                errors.append(f"[Check 1 Schema] decision {d.get('decision_id')} 缺少必填欄位。")
            if set(d.keys()) - allowed_dec:
                errors.append(f"[Check 1 Schema] decision {d.get('decision_id')} 含未允許欄位。")
            if d.get("action") not in _VALID_ACTIONS:
                errors.append(f"[Check 1 Schema] decision {d.get('decision_id')} action 無效：{d.get('action')!r}")
            tw = d.get("target_weight")
            if not isinstance(tw, (int, float)) or not (0.0 <= float(tw) <= 0.25):
                errors.append(
                    f"[Check 1 Schema] decision {d.get('decision_id')} target_weight 須介於 0~0.25：{tw!r}"
                )

    # no_trade_decisions (0..30)
    no_trades = dplan.get("no_trade_decisions")
    if not isinstance(no_trades, list) or len(no_trades) > 30:
        errors.append("[Check 1 Schema] no_trade_decisions 必須為長度 0~30 的陣列。")
    else:
        allowed_nt = {"ticker", "reason_refs", "reason"}
        for nt in no_trades:
            if not isinstance(nt, dict) or not {"ticker", "reason_refs"}.issubset(nt.keys()):
                errors.append("[Check 1 Schema] no_trade_decision 缺少 ticker 或 reason_refs。")
                continue
            if set(nt.keys()) - allowed_nt:
                errors.append(f"[Check 1 Schema] no_trade_decision ({nt.get('ticker')}) 含未允許欄位。")

    # orders (0..60)
    orders = dplan.get("orders")
    if not isinstance(orders, list) or len(orders) > 60:
        errors.append("[Check 1 Schema] orders 必須為長度 0~60 的陣列。")
    else:
        allowed_ord = {"ticker", "side", "shares", "decision_ref", "derivation_detail"}
        for o in orders:
            if not isinstance(o, dict) or not {"ticker", "side", "shares", "decision_ref"}.issubset(o.keys()):
                errors.append("[Check 1 Schema] order 缺少必填欄位。")
                continue
            if set(o.keys()) - allowed_ord:
                errors.append(f"[Check 1 Schema] order ({o.get('ticker')}) 含未允許欄位。")
            sh = o.get("shares")
            if isinstance(sh, bool) or not isinstance(sh, int) or sh < LOT_SIZE or sh % LOT_SIZE != 0:
                errors.append(
                    f"[Check 1 Schema] order ({o.get('ticker')}) shares 必須為 >=1000 且為 1000 的整數倍：{sh!r}"
                )

    # agent_metadata
    meta = dplan.get("agent_metadata")
    if not isinstance(meta, dict):
        errors.append("[Check 1 Schema] agent_metadata 必須為 object。")
    else:
        req_meta = {"model_provider", "model_version", "run_started_at", "run_completed_at", "code_version"}
        allowed_meta = req_meta | {"input_tokens", "output_tokens", "tool_use_count"}
        if not req_meta.issubset(meta.keys()):
            errors.append(f"[Check 1 Schema] agent_metadata 缺少必填欄位：{sorted(req_meta - set(meta.keys()))}")
        if set(meta.keys()) - allowed_meta:
            errors.append("[Check 1 Schema] agent_metadata 含未允許欄位。")
        if meta.get("model_provider") not in _VALID_MODEL_PROVIDERS:
            errors.append(f"[Check 1 Schema] model_provider 無效：{meta.get('model_provider')!r}")
        for ts_key in ("run_started_at", "run_completed_at"):
            if not _RE_TIMESTAMP.match(str(meta.get(ts_key, ""))):
                errors.append(f"[Check 1 Schema] agent_metadata.{ts_key} 必須為 +08:00 ISO 時間。")
        if not _RE_CODE_VER.match(str(meta.get("code_version", ""))):
            errors.append(f"[Check 1 Schema] agent_metadata.code_version 格式無效：{meta.get('code_version')!r}")


def _check_2_id_and_reference_chain(
    dplan: dict[str, Any],
    nav_tradable: float,
    prev_holdings: dict[str, int],
    close_prices: dict[str, float],
    errors: list[str],
) -> None:
    sources = dplan.get("sources", [])
    observations = dplan.get("observations", [])
    inferences = dplan.get("inferences", [])
    decisions = dplan.get("decisions", [])
    no_trades = dplan.get("no_trade_decisions", [])
    orders = dplan.get("orders", [])
    mv = dplan.get("market_view", {})

    # 1. 檢查各層 ID 是否從 1 連續遞增無跳號
    src_ids = [s.get("source_id") for s in sources if isinstance(s, dict)]
    expected_src_ids = [f"S{i}" for i in range(1, len(sources) + 1)]
    if src_ids != expected_src_ids:
        errors.append(f"[Check 2 引用鏈] sources ID 必須從 S1 連續遞增無跳號，預期 {expected_src_ids}，實得 {src_ids}")

    obs_ids = [o.get("obs_id") for o in observations if isinstance(o, dict)]
    expected_obs_ids = [f"O{i}" for i in range(1, len(observations) + 1)]
    if obs_ids != expected_obs_ids:
        errors.append(
            f"[Check 2 引用鏈] observations ID 必須從 O1 連續遞增無跳號，預期 {expected_obs_ids}，實得 {obs_ids}"
        )

    inf_ids = [inf.get("inf_id") for inf in inferences if isinstance(inf, dict)]
    expected_inf_ids = [f"I{i}" for i in range(1, len(inferences) + 1)]
    if inf_ids != expected_inf_ids:
        errors.append(
            f"[Check 2 引用鏈] inferences ID 必須從 I1 連續遞增無跳號，預期 {expected_inf_ids}，實得 {inf_ids}"
        )

    dec_ids = [d.get("decision_id") for d in decisions if isinstance(d, dict)]
    expected_dec_ids = [f"D{i}" for i in range(1, len(decisions) + 1)]
    if dec_ids != expected_dec_ids:
        errors.append(
            f"[Check 2 引用鏈] decisions ID 必須從 D1 連續遞增無跳號，預期 {expected_dec_ids}，實得 {dec_ids}"
        )

    src_set = set(src_ids)
    obs_set = set(obs_ids)
    inf_set = set(inf_ids)
    dec_map = {d.get("decision_id"): d for d in decisions if isinstance(d, dict)}

    # 2. 檢查各層引用是否 100% 存在
    for o in observations:
        if not isinstance(o, dict):
            continue
        refs = o.get("source_ref", [])
        if not refs:
            errors.append(f"[Check 2 引用鏈] observation {o.get('obs_id')} source_ref 不可為空。")
        for r in refs:
            if r not in src_set:
                errors.append(f"[Check 2 引用鏈] observation {o.get('obs_id')} 引用了不存在的 source_id {r!r}。")

    if isinstance(mv, dict):
        basis_refs = mv.get("basis_refs", [])
        if not basis_refs:
            errors.append("[Check 2 引用鏈] market_view.basis_refs 不可為空。")
        for r in basis_refs:
            if r not in obs_set:
                errors.append(f"[Check 2 引用鏈] market_view.basis_refs 引用了不存在的 obs_id {r!r}。")

    for inf in inferences:
        if not isinstance(inf, dict):
            continue
        refs = inf.get("premise_refs", [])
        if not refs:
            errors.append(f"[Check 2 引用鏈] inference {inf.get('inf_id')} premise_refs 不可為空。")
        for r in refs:
            if r not in obs_set:
                errors.append(f"[Check 2 引用鏈] inference {inf.get('inf_id')} 引用了不存在的 obs_id {r!r}。")

    for d in decisions:
        if not isinstance(d, dict):
            continue
        refs = d.get("inference_refs", [])
        if not refs:
            errors.append(f"[Check 2 引用鏈] decision {d.get('decision_id')} inference_refs 不可為空。")
        for r in refs:
            if r not in inf_set:
                errors.append(f"[Check 2 引用鏈] decision {d.get('decision_id')} 引用了不存在的 inf_id {r!r}。")

    for nt in no_trades:
        if not isinstance(nt, dict):
            continue
        refs = nt.get("reason_refs", [])
        if not refs:
            errors.append(f"[Check 2 引用鏈] no_trade_decision ({nt.get('ticker')}) reason_refs 不可為空。")
        for r in refs:
            if r not in inf_set:
                errors.append(
                    f"[Check 2 引用鏈] no_trade_decision ({nt.get('ticker')}) 引用了不存在的 inf_id {r!r}。"
                )

    # 3. 檢查 orders 與 decisions 的 1:1 對應及官方公式一致性
    order_dec_refs: set[str] = set()
    for o in orders:
        if not isinstance(o, dict):
            continue
        dref = o.get("decision_ref")
        if dref not in dec_map:
            errors.append(f"[Check 2 引用鏈] order ({o.get('ticker')}) 引用了不存在的 decision_ref {dref!r}。")
            continue
        order_dec_refs.add(dref)
        dec = dec_map[dref]
        ticker = dec.get("ticker")
        if o.get("ticker") != ticker:
            errors.append(
                f"[Check 2 引用鏈] order decision_ref={dref} 的 ticker ({o.get('ticker')}) 與 decision ({ticker}) 不符。"
            )
            continue

        price = close_prices.get(ticker, 0.0)
        if price > 0 and nav_tradable > 0:
            cur_sh = int(prev_holdings.get(ticker, 0))
            target_sh = compute_target_shares(
                float(dec.get("target_weight", 0.0)), nav_tradable, price, str(dec.get("action"))
            )
            expected_delta = target_sh - cur_sh
            expected_side = "BUY" if expected_delta > 0 else "SELL"
            expected_shares = abs(expected_delta)
            if o.get("side") != expected_side or o.get("shares") != expected_shares:
                errors.append(
                    f"[Check 2 引用鏈] order ({ticker}, {dref}) 與官方推導公式不一致："
                    f"預期 {expected_side} {expected_shares} 股，實得 {o.get('side')} {o.get('shares')} 股。"
                )

    missing_orders_for_dec = set(dec_map.keys()) - order_dec_refs
    if missing_orders_for_dec:
        errors.append(
            f"[Check 2 引用鏈] 存在無對應委託單之異動決策 (可能因換算後為 0 股未移回 no_trade_decisions)："
            f"{sorted(missing_orders_for_dec)}"
        )


def _check_3_holdings_coverage(
    dplan: dict[str, Any],
    prev_holdings: dict[str, int],
    errors: list[str],
) -> None:
    held_yesterday = {t for t, sh in prev_holdings.items() if sh > 0}
    decisions = dplan.get("decisions", [])
    no_trades = dplan.get("no_trade_decisions", [])

    dec_tickers: list[str] = [d.get("ticker", "") for d in decisions if isinstance(d, dict)]
    nt_tickers: list[str] = [nt.get("ticker", "") for nt in no_trades if isinstance(nt, dict)]

    if len(dec_tickers) != len(set(dec_tickers)):
        errors.append(f"[Check 3 持倉覆蓋] decisions 中出現重複的股票代號：{dec_tickers}")
    if len(nt_tickers) != len(set(nt_tickers)):
        errors.append(f"[Check 3 持倉覆蓋] no_trade_decisions 中出現重複的股票代號：{nt_tickers}")

    overlap = set(dec_tickers) & set(nt_tickers)
    if overlap:
        errors.append(
            f"[Check 3 持倉覆蓋] 同一檔股票不可同時出現在 decisions 與 no_trade_decisions：{sorted(overlap)}"
        )

    covered_holdings: set[str] = set()
    for d in decisions:
        if not isinstance(d, dict):
            continue
        t = d.get("ticker", "")
        act = d.get("action", "")
        if act == "BUY":
            if t in held_yesterday:
                errors.append(f"[Check 3 持倉覆蓋] 股票 {t} 昨日已有持倉，加碼應使用 'ADD' 而非 'BUY'。")
        elif act in ("ADD", "TRIM", "SELL_ALL"):
            if t not in held_yesterday:
                errors.append(f"[Check 3 持倉覆蓋] 股票 {t} 昨日無持倉 (shares=0)，不可執行 {act}。")
            else:
                covered_holdings.add(t)

    for nt in no_trades:
        if not isinstance(nt, dict):
            continue
        t = nt.get("ticker", "")
        if t not in held_yesterday:
            errors.append(f"[Check 3 持倉覆蓋] no_trade_decisions 包含昨日未持有之股票：{t}。")
        else:
            covered_holdings.add(t)

    missing_holdings = held_yesterday - covered_holdings
    if missing_holdings:
        errors.append(
            f"[Check 3 持倉覆蓋] 昨日持倉未達 100% 覆蓋！遺漏以下昨日持股：{sorted(missing_holdings)}"
        )


def _check_4_and_5_universe_count_and_weights(
    dplan: dict[str, Any],
    nav_tradable: float,
    prev_holdings: dict[str, int],
    close_prices: dict[str, float],
    errors: list[str],
) -> tuple[dict[str, int], dict[str, float]]:
    decisions = dplan.get("decisions", [])
    no_trades = dplan.get("no_trade_decisions", [])
    orders = dplan.get("orders", [])

    # Check 4a: 所有出現的股票代號必須在 150 檔白名單內
    all_mentioned = (
        {d.get("ticker", "") for d in decisions if isinstance(d, dict)}
        | {nt.get("ticker", "") for nt in no_trades if isinstance(nt, dict)}
        | {o.get("ticker", "") for o in orders if isinstance(o, dict)}
    )
    for t in sorted(all_mentioned):
        if not is_valid_ticker(t):
            errors.append(f"[Check 4 白名單] 股票代號 {t!r} 不在官方 150 檔白名單 (UNIVERSE_150) 中！")

    # 計算成交後預計股數
    post_shares: dict[str, int] = {t: int(sh) for t, sh in prev_holdings.items() if sh > 0}
    for o in orders:
        if not isinstance(o, dict):
            continue
        t = o.get("ticker", "")
        side = o.get("side", "")
        sh = int(o.get("shares", 0))
        cur = post_shares.get(t, 0)
        if side == "BUY":
            post_shares[t] = cur + sh
        elif side == "SELL":
            if sh > cur:
                errors.append(
                    f"[Check 5 庫存上限] 股票 {t} 賣出股數 ({sh}) 超過昨日庫存股數 ({cur})，禁止放空！"
                )
            post_shares[t] = cur - sh

    active_tickers = [t for t, sh in post_shares.items() if sh > 0]
    active_count = len(active_tickers)
    if not (MIN_HOLDINGS_COUNT <= active_count <= MAX_HOLDINGS_COUNT):
        errors.append(
            f"[Check 4 持股檔數] 交易後預計持股檔數為 {active_count} 檔，違反官方 {MIN_HOLDINGS_COUNT}~{MAX_HOLDINGS_COUNT} 檔規定！"
        )

    # Check 5: 檢查 target_weight 與成交後預估權重上限 (2330 <= 25%, 其餘 <= 10%)
    for d in decisions:
        if not isinstance(d, dict):
            continue
        t = d.get("ticker", "")
        tw = float(d.get("target_weight", 0.0))
        if is_valid_ticker(t):
            max_w = get_max_weight(t)
            if tw > max_w + 1e-9:
                errors.append(
                    f"[Check 5 權重上限] decision ({t}) target_weight={tw:.4f} 超過單一權重上限 {max_w:.2f}！"
                )

    post_weights: dict[str, float] = {}
    if nav_tradable > 0:
        for t in active_tickers:
            price = close_prices.get(t, 0.0)
            w = (post_shares[t] * price) / nav_tradable
            post_weights[t] = round(w, 6)
            if is_valid_ticker(t):
                max_w = get_max_weight(t)
                if w > max_w + 1e-6:
                    errors.append(
                        f"[Check 5 權重上限] 股票 {t} 預計成交後權重為 {w:.4f}，超過單一權重上限 {max_w:.2f}！"
                    )

    return post_shares, post_weights


def _check_6_and_7_cash_posture_and_funding(
    dplan: dict[str, Any],
    nav_tradable: float,
    prev_cash: float,
    close_prices: dict[str, float],
    errors: list[str],
) -> dict[str, float]:
    orders = dplan.get("orders", [])
    decisions = dplan.get("decisions", [])
    mv = dplan.get("market_view", {})
    posture = mv.get("posture", {}) if isinstance(mv, dict) else {}

    buy_amt = 0.0
    sell_amt = 0.0
    for o in orders:
        if not isinstance(o, dict):
            continue
        t = o.get("ticker", "")
        sh = int(o.get("shares", 0))
        price = float(close_prices.get(t, 0.0))
        if o.get("side") == "BUY":
            buy_amt += sh * price
        elif o.get("side") == "SELL":
            sell_amt += sh * price

    fee_and_tax = (
        buy_amt * BUY_COMMISSION_RATE
        + sell_amt * (SELL_COMMISSION_RATE + SELL_TAX_RATE)
    )
    est_cash = prev_cash - buy_amt + sell_amt - fee_and_tax
    est_cash_ratio = est_cash / nav_tradable if nav_tradable > 0 else 0.0

    # Check 6a: 現金不得為負，且預估現金比須介於 [0%, 25%]
    if est_cash < 0:
        errors.append(
            f"[Check 6 現金水位] 扣除手續費與證交稅後預估現金為負數 ({est_cash:,.0f} 元)！請調降買進權重或增加賣出。"
        )
    if not (MIN_CASH_RATIO <= est_cash_ratio <= MAX_CASH_RATIO):
        errors.append(
            f"[Check 6 現金水位] 預估稅後現金比例為 {est_cash_ratio:.4f} ({est_cash_ratio*100:.2f}%)，"
            f"違反官方 0%~25% 硬性上限！"
        )

    # Check 6b: 預估現金比須落在 posture.target_cash_pct_range 內
    rng = posture.get("target_cash_pct_range", [0.0, 0.25])
    if isinstance(rng, list) and len(rng) == 2:
        low, high = float(rng[0]), float(rng[1])
        if not (low - 1e-6 <= est_cash_ratio <= high + 1e-6):
            errors.append(
                f"[Check 6 現金區間] 預估稅後現金比 {est_cash_ratio:.4f} 未落在 "
                f"posture.target_cash_pct_range [{low:.4f}, {high:.4f}] 區間內！"
            )
    else:
        low, high = 0.0, 0.25

    # Check 7a: posture.net_exposure_intent 與買賣超淨流向一致性
    intent = posture.get("net_exposure_intent")
    if intent == "increase" and not (buy_amt > sell_amt):
        errors.append(
            f"[Check 7 Posture] net_exposure_intent 為 'increase'，但買進總額 ({buy_amt:,.0f}) 未大於賣出總額 ({sell_amt:,.0f})！"
        )
    elif intent == "reduce" and not (sell_amt > buy_amt):
        errors.append(
            f"[Check 7 Posture] net_exposure_intent 為 'reduce'，但賣出總額 ({sell_amt:,.0f}) 未大於買進總額 ({buy_amt:,.0f})！"
        )
    elif intent == "hold":
        limit = HOLD_NET_FLOW_TOLERANCE * nav_tradable
        if abs(buy_amt - sell_amt) > limit + 1e-2:
            errors.append(
                f"[Check 7 Posture] net_exposure_intent 為 'hold'，但買賣超淨流向差額 ({abs(buy_amt - sell_amt):,.0f}) "
                f"超過 2% NAV 容差 ({limit:,.0f})！"
            )

    # Check 7b: funding_for 引用合法性與缺口存在性
    dec_map = {d.get("decision_id"): d for d in decisions if isinstance(d, dict)}
    funding_sell_ids: set[str] = set()
    for d in decisions:
        if not isinstance(d, dict) or "funding_for" not in d:
            continue
        did = d.get("decision_id", "")
        act = d.get("action", "")
        if act not in ("TRIM", "SELL_ALL"):
            errors.append(
                f"[Check 7 資金調度] decision {did} (action={act}) 不可標註 funding_for，僅限 TRIM/SELL_ALL！"
            )
        f_refs = d.get("funding_for", [])
        if not isinstance(f_refs, list) or len(f_refs) == 0:
            errors.append(f"[Check 7 資金調度] decision {did} funding_for 不可為空陣列。")
        for target_id in f_refs:
            target_dec = dec_map.get(target_id)
            if target_dec is None or target_dec.get("action") not in ("BUY", "ADD"):
                errors.append(
                    f"[Check 7 資金調度] decision {did} funding_for 引用之 {target_id!r} 不存在或非 BUY/ADD 決策！"
                )
        funding_sell_ids.add(did)

    if funding_sell_ids and nav_tradable > 0:
        non_funding_sell_amt = sum(
            int(o.get("shares", 0)) * float(close_prices.get(o.get("ticker", ""), 0.0))
            for o in orders
            if isinstance(o, dict)
            and o.get("side") == "SELL"
            and o.get("decision_ref") not in funding_sell_ids
        )
        cost_without = (
            buy_amt * BUY_COMMISSION_RATE
            + non_funding_sell_amt * (SELL_COMMISSION_RATE + SELL_TAX_RATE)
        )
        cash_without = prev_cash - buy_amt + non_funding_sell_amt - cost_without
        ratio_without = cash_without / nav_tradable
        if ratio_without >= low - 1e-9:
            errors.append(
                f"[Check 7 資金調度] 違反『缺口存在性』：不執行 funding_for 賣出時，預估現金比為 {ratio_without:.4f}，"
                f"並未低於 target_cash_pct_range 下限 ({low:.4f})，屬於『錢夠卻宣稱調度』！"
            )

    return {
        "buy_amount": round(buy_amt, 2),
        "sell_amount": round(sell_amt, 2),
        "net_flow": round(buy_amt - sell_amt, 2),
        "fee_and_tax": round(fee_and_tax, 2),
        "estimated_cash": round(est_cash, 2),
        "estimated_cash_ratio": round(est_cash_ratio, 6),
    }


def _check_8_active_share(
    post_weights: dict[str, float],
    etf_benchmarks: dict[str, dict[str, float]],
    errors: list[str],
) -> float:
    min_as = 1.0
    min_etf = ""
    for etf_code, bench_w in etf_benchmarks.items():
        as_val = compute_active_share(post_weights, bench_w)
        if as_val < min_as:
            min_as = as_val
            min_etf = etf_code

    if min_as < MIN_ACTIVE_SHARE:
        errors.append(
            f"[Check 8 Active Share] 與主動型 ETF {min_etf} 之 Active Share 僅 {min_as:.4f} ({min_as*100:.2f}%)，"
            f"低於官方 15% 下限！"
        )
    return round(min_as, 6)
