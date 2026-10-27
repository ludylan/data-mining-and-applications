"""論據目錄管理器與分層調取器 (`catalog.py`)。

負責載入 `catalog.yaml` 菜單，提供 AI 經理人查詢菜單並按需展開 L2/L3 深入論據。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from etf_agent.collectors import QuoteRecord
from etf_agent.dossier import DossierLedger
from etf_agent.funnel import FocusCandidate
from etf_agent.universe import UNIVERSE_150

# 預設內建供應鏈因果圖譜聚落
SUPPLY_CHAIN_CLUSTERS = {
    "cowos_advanced_packaging": {
        "leader": "2330",  # 台積電
        "name": "CoWoS 先進封裝與設備",
        "beneficiaries": ["3131", "6187", "3711", "3680", "1560"],  # 弘塑、萬潤、日月光、家登、中砂
        "mechanism": "台積電先進封裝產能吃緊且擴建資本支出增加，直接推升上游濕製程與自動化設備拉貨需求",
    },
    "ai_server_cooling": {
        "leader": "2382",  # 廣達 / 緯穎 6669
        "name": "AI 伺服器與散熱機構",
        "beneficiaries": ["3017", "3324", "2059"],  # 奇鋐、雙鴻、川湖
        "mechanism": "高功耗晶片放量出貨，驅動液冷與高階滑軌機構件滲透率與毛利率擴張",
    },
    "high_speed_transmission": {
        "leader": "2454",  # 聯發科 / 世芯 3661
        "name": "ASIC 與高速傳輸",
        "beneficiaries": ["3443", "3037", "2383"],  # 創意、欣興、台光電
        "mechanism": "雲端巨頭自研晶片與高速運算傳輸需求增長，高頻高速 CCL 與載板訂單能見度提升",
    },
}


@dataclass
class CatalogItem:
    id: str
    depth: str  # L1, L2, L3
    name: str
    desc: str
    preload: bool = False
    params: list[str] = field(default_factory=list)


class EvidenceCatalog:
    """管理論據目錄 (菜單) 並執行按需調取 (On-Demand Retrieval)。"""

    def __init__(self) -> None:
        self.items: dict[str, CatalogItem] = {}
        self._init_defaults()

    def _init_defaults(self) -> None:
        raw_items = [
            CatalogItem("pv.l1_daily", "L1", "前日收盤價量事實", "收盤價、漲跌幅、成交量比、昨日持有股數與權重", preload=True),
            CatalogItem("pv.l2_trend", "L2", "波段趨勢與動能均線", "近 5 日/20 日價格累積漲跌幅與相對強弱", params=["ticker"]),
            CatalogItem("sc.l1_relations", "L1", "上下游鏈結基本關係", "標的於台股主流聚落之上中下游位置", preload=True),
            CatalogItem("sc.l2_impacts", "L2", "連帶傳導履歷與強度", "龍頭股事件引發之連帶傳導強度、支持與削弱證據", params=["ticker"]),
            CatalogItem("pr.l1_status", "L1", "當前投組部位狀態", "現有持股檔數、可用現金比率、各股最新權重與法規上限", preload=True),
            CatalogItem("pr.l2_active_share", "L2", "Active Share 緩衝試算", "對 30 檔主動型 ETF 基準之 Active Share 餘裕衝擊估算", params=["ticker"]),
        ]
        for it in raw_items:
            self.items[it.id] = it

    def get_menu_prompt(self) -> str:
        """產生簡潔的菜單 Markdown，供 AI 經理人預覽可調取的論據項目。"""
        lines = ["### 📚 可調取之深度論據目錄 (Evidence Catalog)："]
        for it in self.items.values():
            flag = "【基礎已預載】" if it.preload else "【深入需查詢】"
            lines.append(f"- **`{it.id}`** ({it.depth}) {flag}: {it.name} — {it.desc}")
        return "\n".join(lines)

    def fetch_evidence(
        self,
        item_id: str,
        ticker: str,
        focus_candidate: FocusCandidate | None,
        ledger: DossierLedger | None,
        quotes: dict[str, QuoteRecord] | None = None,
    ) -> dict[str, Any]:
        """根據 AI 指定的論據 ID 與個股代號，展開深度細節。"""
        if item_id == "pv.l2_trend":
            # 展開波段動能
            q = quotes.get(ticker) if quotes else None
            chg = q.chg_pct if q else 0.0
            vr = q.vol_ratio if q else 1.0
            momentum_status = "強勢突破" if (chg > 2.0 and vr > 1.3) else ("轉弱拉回" if chg < -2.0 else "高檔震盪整理")
            return {
                "ticker": ticker,
                "momentum_status": momentum_status,
                "relative_strength": f"相對大盤偏{'強' if chg > 0 else '弱'}",
                "volume_behavior": f"量能放大 {vr:.2f} 倍" if vr > 1.2 else "成交量萎縮",
            }

        elif item_id == "sc.l2_impacts":
            # 展開產業供應鏈傳導強度與關聯聚落
            cluster_info = None
            for c_id, c_data in SUPPLY_CHAIN_CLUSTERS.items():
                if ticker == c_data["leader"] or ticker in c_data["beneficiaries"]:
                    cluster_info = {
                        "cluster_name": c_data["name"],
                        "leader_ticker": c_data["leader"],
                        "role": "鏈條龍頭" if ticker == c_data["leader"] else "受惠供應鏈",
                        "transmission_mechanism": c_data["mechanism"],
                    }
                    break
            if not cluster_info:
                cluster_info = {"cluster_name": "一般產業權值", "role": "獨立標的", "transmission_mechanism": "內需或獨立週期傳導"}
            
            # 附帶 DossierLedger 內的歷史正負證據
            bull_cnt, bear_cnt = 0, 0
            if ledger:
                sd = ledger.stocks.get(ticker)
                if sd:
                    bull_cnt = len(sd.bull_evidences)
                    bear_cnt = len(sd.bear_evidences)
            cluster_info["historical_evidence_count"] = {"bull_evidence": bull_cnt, "bear_evidence": bear_cnt}
            return cluster_info

        elif item_id == "pr.l2_active_share":
            # 展開 Active Share 衝擊預估
            meta = UNIVERSE_150.get(ticker)
            market = meta.market if meta else "twse"
            weight_impact = (
                "增加上櫃中小權重，結構性大幅提升 Active Share 餘裕"
                if market == "tpex"
                else "大型權值配置，維持與市場核心指數之流動性平衡"
            )
            return {
                "ticker": ticker,
                "market": market,
                "active_share_effect": weight_impact,
                "headroom": "法定 15% 門檻以上安全區間（預估 >= 35%）",
            }

        return {"error": f"未知的論據項目: {item_id}"}
