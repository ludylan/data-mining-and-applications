# M3 全市場掃描漏斗與事實匯流排規格書 (`M3_funnel_spec.md`)

> **上游位置**：對應 [`docs/MAP.md`](../MAP.md) 中的 **模組 3 (`M3_funnel`)**  
> **對應下游程式**：`src/etf_agent/funnel.py`

---

## 1. 模組職責與為什麼需要它？
1. **解決 Schema 筆數上限與 Token 爆炸問題**：官方規定 `observations` $\le 100$ 筆、`inferences` $\le 60$ 筆。我們不能把 150 檔全部塞進報告，必須透過漏斗挑出當日最關鍵的 **約 30 ～ 38 檔焦點名單（Focus List）**。
2. **杜絕 AI 數字幻覺與引用斷環**：L0 `sources (S1..Sn)` 與 L1 `observations (O1..On)` **完全由這個 Python 模組產生並編好連續流水號**。下游的 AI 只准引用這裡編好的 `O1..On`，不准自己捏造事實或更改 `values` 裡的數字！

---

## 2. 內部子模組詳細規格

### 子模組 3.1：既有持股強制保留器 (`retain_current_holdings`)
* **規則依據**：昨日 `DataPack.holdings` 中所有 `shares > 0` 的股票（假設 24 檔），今天 100% 必須交代（`decisions` 或 `no_trade_decisions`），且每一筆都必須引用至少一個 `Inference (I)`，而每個 `I` 必須引用至少一個 `Observation (O)`。
* **動作**：將現有持股全部無條件列入今日 Focus List，確保每一檔現有持股都至少有 1 筆對應的 `Observation (O)`（包含昨收價、漲跌幅、目前權重）。

### 子模組 3.2：全市場異動雷達 (`scan_market_anomalies`)
* 對 150 檔計算簡易動能與事件評分（Anomaly Score）：
  * 當日或近 5 日漲跌幅強於大盤、成交量放大、或 `extra_signals` 中有財報／新聞正向訊號。
* 找出今日表現最突出的前 N 檔領頭羊（`leader`）與強勢股。

### 子模組 3.3：供應鏈因果傳導器 (`propagate_supply_chain`)
* **核心策略實現**：
  * 當某個產業聚落（例如 `SC1_SEMI_EQUIP` 或 `SC2_AI_SERVER`）的 `leader` 出現顯著正向訊號時，自動從 `M1` 供應鏈圖譜中，挑出同聚落中**「漲幅相對滯後（尚未完全反映）之上櫃 `tpex` 或中小型 `follower` 個股」**。
  * **首日建倉模式（Day 1，現有持股 0 檔）**：直接從 6 大聚落中按比例選出約 **30 檔候選股（上市約 16 檔 + 上櫃約 14 檔）** 進入 Focus List，供 AI 挑選 24 檔建倉。
  * **日常調倉模式（Day 2~24，現有持股 ~24 檔）**：保留現有 24 檔 ＋ 新增 8～12 檔「異動與供應鏈傳導候選股」，合計約 **32～36 檔** 進入 Focus List。

### 子模組 3.4：L0/L1 事實鎖定與連續編號器 (`build_sources_and_observations`)
* **自動去重與連續編號**：
  * 將所有用到的資料來源去重，依序編號為 `S1, S2, ..., Sk`（嚴格從 1 開始連續遞增，不加前導零，上限 $\le 50$）。
  * 產生總經級事實 `O1..Om`（供 `market_view.basis_refs` 引用）。
  * 為 Focus List 中的每一檔股票產生個股級事實 `O(m+1)..On`（上限 $\le 100$）。
* **客觀陳述模板（防主觀字眼違規）**：
  * `statement` 僅由客觀數據組合（例如：`"2330 前一交易日收盤價 2585.0 元，單日變動 +1.5%，成交量 32400 張"`），絕不包含「看好、強勢、建議買進」等推論詞彙。
  * `values` 嚴格限制為 `dict[str, float | int]`，且至少 1 個鍵值對。
