# 衝刺行動清單 (Sprint Action Checklist)

> **開發原則**：小步快跑、逐步驗證。每次只完成一個最小單元，跑通測試並由你確認後，再推進下一步。

---

## 🚨 第一波（Sprint 1）：打通 100% 合規交卷最小閉環（MVP）

- [x] **Step 1.1：建立 `M1` 底層名單與 `M1.5` 連結履歷庫 (`src/etf_agent/universe.py` & `dossier.py`)**
  - 收錄官方 150 檔完整白名單（上市 100 檔 `twse` ＋ 上櫃 50 檔 `tpex`）與 `max_weight`（2330 為 0.25，其餘為 0.10）。
  - 內建 30 檔主動型 ETF 清單與 Active Share $\ge 15\%$ 驗算函式。
  - 建立 Python 規則可行範圍 `[min_bound, max_bound]` 與正反證據履歷庫 (`StockDossier` / `LinkDossier`)。
  - 撰寫單元測試 `tests/test_universe.py` 驗證通過。
- [x] **Step 1.2：建立 `M5` 沙盒試算與合規守門員 (`src/etf_agent/sandbox.py`)**
  - 實作官方千股無條件捨去公式推導 `orders`（含 0 股委託自動移回 `no_trade_decisions` 與 `D1..Dk` 重編碼）。
  - 實作 `D-Plan.schema_v4.3.json` 驗證與 8 大硬規則驗算（檔數、權重、現金區間、引用鏈 0 斷環、100% 持股覆蓋、`posture` 與 `funding_for` 數學一致性、Active Share $\ge 15\%$）。
  - 實作 `align_posture_and_funding` 自動校準現金區間與拔除多餘 `funding_for`，並通過 `tests/test_sandbox.py` 全數單元測試。
- [x] **Step 1.3：建立 `M2 + M3` 資料讀取與事實編號器 (`src/etf_agent/collectors.py` & `funnel.py`)**
  - 解析官方 `DataPack.json`（自動判別 Day 1 全現金建倉 vs Day 2+ 日常調倉，並攔截 `prev_settlement` 未結算狀態）。
  - 結合 `DossierLedger` 產生 ~35 檔 Focus List，並自動編排連續不跳號之 `S1..Sn` 與 `O1..On`，通過 `tests/test_funnel.py` 測試。
- [x] **Step 1.4：建立 `M4` PM Agent 決策與 LangGraph 回推迴圈 (`src/etf_agent/pm_agent.py` & `graph.py`)** 👈 **(本輪完成，Sprint 1 MVP 全線貫通！)**
  - 串接 `PM Agent ⇄ Sandbox Verifier` 迴圈，收到沙盒退回時自動讀取「退回原因 + 補齊指引」重構，一鍵輸出 100% 合格之 `D-Plan_<team>_<date>.json`，並通過 `tests/test_dplan_e2e.py` 首日建倉與次日調倉端到端測試。

---

## ⚡ 第二波（Sprint 2）：接上真實外部行情 API（10/25～10/26 開賽前）

- [ ] **Step 2.1**：串接 TWSE 證交所與 TPEx 櫃買中心免 Key OpenAPI（含 `yfinance` 自動備援），自動抓取 150 檔最新收盤價量。
- [ ] **Step 2.2**：串接隔夜美股指標（`^SOX`、`^IXIC`、`TSM` ADR）供 `market_view` 引用。
- [ ] **Step 2.3**：優化異動雷達與供應鏈傳導評分邏輯。

---

## 💎 第三波（Sprint 3）：外掛加料與課堂 Lab 整合（10/27 開賽後）

- [ ] **Step 3.1**：開放 `inputs/extra_signals/` 讀取課堂 Lab 財報／NLP 統計訊號與 MOPS 月營收。
- [ ] **Step 3.2**：加入昨日成交復盤反思記憶（Reflection）與反方證據強化。
