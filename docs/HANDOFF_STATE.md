# 專案交接與無縫接軌狀態卡 (Project Handoff State)

> **產生時間**：2026-10-28 00:15  
> **專案定位**：AI CUP 2026 玉山人工智慧挑戰賽 — Agent 基金經理人 (Team 11715)  
> **工作目錄**：`d:\etf-agent` (Git Remote: `origin/main`)  
> **使用模型**：Google Gemini 3.8 Flash (金鑰已設於 `.env`)  

---

## 1. 目前完成狀態總結 (Current Milestone)
* **Sprint 1 (閉環 MVP)**：已 100% 完工並通過 19 項自動化單元測試 (`pytest -q` 全部綠燈)。
* **Day 1 (2026-10-27 首日建倉)**：已成功執行，24 檔 BUY 全數成交，0 違規，D-Plan 與 HTML 儀表板已產出。
* **Day 2 (2026-10-28 日常調倉)**：已成功執行，展現 AI 汰弱留強 (出清 5880 合庫金，買進 3081 聯亞)，沙盒八大鐵律 100% 通過。
* **階段 A (AI 自主提案權與論據目錄)**：已落地！
  - `src/etf_agent/catalog.yaml` & `catalog.py`：論據目錄 (L1 預載，L2/L3 按需展開)。
  - `src/etf_agent/impact_ledger.py`：重大因果鏈影響帳本。
  - `inputs/etf_benchmarks.json`：整合 MoneyDJ 30 檔主動 ETF 真實持股，沙盒 Active Share 檢核實測通過 (最低 26.8% >= 15% 紅線)。
  - `inputs/datapacks/`：資料包統一存放區，`run_daily.py` 支援自動偵測最新檔案。

---

## 2. 檔案目錄結構與核心職責
* `inputs/datapacks/`：大會每日下載之 `DataPack_..._YYYY-MM-DD.json`（自動選取最新）。
* `inputs/etf_benchmarks.json`：30 檔主動 ETF 成分股基準庫。
* `src/etf_agent/universe.py`：150 檔個股白名單、權重上限 (2330 25%，其餘 10%)。
* `src/etf_agent/collectors.py`：TWSE/TPEx OpenAPI 150 檔最新收盤行情抓取。
* `src/etf_agent/funnel.py`：S1..Sn / O1..On 連續編號器、昨日持倉 100% 保留漏斗。
* `src/etf_agent/pm_agent.py`：Gemini 3.8 Flash 自主提案核心 (BUY/ADD/TRIM/SELL_ALL/HOLD)。
* `src/etf_agent/sandbox.py`：沙盒守門員 (千股反推、現金比率、檔數、Active Share 檢核)。
* `src/etf_agent/dashboard.py`：產出人類可讀的 HTML 戰情儀表板。
* `scripts/run_daily.py`：每日一鍵執行入口 (`python scripts/run_daily.py`)。

---

## 3. 下一步迭代計畫 (Next Steps - 階段 B & C)
對應規格書：`docs/AI_AGENT_PLAN.md`
1. **階段 B (影響帳本跨日記憶與累積)**：
   - 讓 `impact_ledger.py` 與 `dossier_ledger.json` 在每日啟動時主動讀取昨日記錄，落實跨日因果學習與衰減。
2. **階段 C (外部訊號擴充)**：
   - 在 `inputs/extra_signals/` 引入三大法人每日買賣超或月營收資訊。
