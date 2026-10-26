# M2 資料收集與快照層規格書 (`M2_collectors_spec.md`)

> **上游位置**：對應 [`docs/MAP.md`](../MAP.md) 中的 **模組 2 (`M2_collectors`)**  
> **對應下游程式**：`src/etf_agent/collectors.py`

---

## 1. 模組職責與邊界
負責把外部世界與隊友提供的資料，統一轉換成乾淨、帶有真實網址（`url`）與時間戳記（`content_as_of`）的標準結構，並存下原始快照以供日後抽檢。

---

## 2. 內部子模組詳細規格

### 子模組 2.1：官方 `DataPack.json` 解析器 (`parse_datapack`)
* **輸入**：每日從比賽後台下載的 `DataPack_<team_id>_<trade_date>.json`。
* **邊界防呆檢查**：
  1. 若 `"prev_settlement" in datapack["missing"]` 或 `datapack["account"] is None`：代表前一日結算尚未完成，**立即中止執行並印出大字警告「請等待官方結算完成後重新下載 DataPack」**（避免把 `null` 當成空手去亂買！）。
  2. 若 `as_of_date is None` 且 `holdings == []` 且 `account.cash == 1_000_000_000`：判定為 **Day 1 首日建倉模式**。
  3. 記錄每檔現有持股 (`shares > 0`) 的 `code`、`shares`、`close_price`、`tradable_weight`，並檢查是否有個股因自然上漲導致 `tradable_weight > max_weight`（追蹤連續超限天數，供 5 日寬限期規則使用）。

### 子模組 2.2：雙市場 150 檔行情收集器 (`fetch_market_quotes`)
* **為什麼必備**：計算 L4 `orders` 公式需要所有目標股票的「前日收盤價（`close_price`）」。`DataPack` 只有現有持股的收盤價，要買新股就必須有全市場 150 檔收盤價。
* **三層備援機制（Fallback Chain）**：
  1. **優先 1（官方 OpenAPI）**：
     * 上市 (`twse`)：`https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`
     * 上櫃 (`tpex`)：`https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes`
  2. **優先 2（`yfinance` 自動備援）**：若官方 API 維護或超時，自動以 `ticker.TW` / `ticker.TWO` 補齊收盤價與漲跌幅。
  3. **優先 3（本機覆蓋檔 `inputs/prices_override.csv`）**：允許手動放入價格表。

### 子模組 2.3：總體經濟與夜盤指標收集器 (`fetch_macro_indicators`)
* 抓取費城半導體 (`^SOX`)、那斯達克 (`^IXIC`)、台積電 ADR (`TSM`)、加權指數 (`^TWII`) 之最近收盤價與單日漲跌幅（%）。
* 這些數據將固定成為 `O1, O2, O3`，專門提供給 D-Plan 的 **`market_view.basis_refs`** 引用！

### 子模組 2.4：課堂 Lab 與隊友外掛訊號讀取器 (`load_extra_signals`)
* **放置路徑**：`inputs/extra_signals/*.json` 或 `*.csv`（或是隊友放在 `contributions/` 經整合後的腳本輸出）。
* **統一契約格式 (`AnalystCard`)**：
  隊友的財報腳本或新聞情緒腳本，只要輸出包含以下欄位的 JSON 清單，系統就會自動吸收：
  ```json
  [
    {
      "ticker": "2330",
      "authority": "mops",
      "url": "https://mops.twse.com.tw/mops/web/t05st10_ifrs",
      "content_as_of": "2026-10-26T18:00:00+08:00",
      "topic": "monthly_revenue",
      "statement": "2330 最新公布月營收年增 34.2%，創歷年同期新高",
      "values": { "rev_yoy_pct": 34.2 },
      "signal_hint": "bullish"
    }
  ]
  ```

### 子模組 2.5：原始資料快照器 (`save_snapshots`)
* 每次抓取完成後，自動將當日原始資料存於 `runs/<trade_date>/source_snapshots/`，確保主辦方抽檢 `values` 數值時 100% 有據可查。
