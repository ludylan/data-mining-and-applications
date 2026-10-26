# 系統上游主幹與子模組詳細架構圖 (Master Architecture & Sub-module Map)

> **本文件的定位（程式碼的上游）**：  
> 這裡記錄我們與隊友討論出的**所有模組邊界、內部子模組、資料流向與待決議細節**。  
> 任何想法先在這裡（以及 `docs/specs/` 下的各模組規格檔）寫清楚、畫進圖裡，才往下游寫成程式碼。這樣就算過了幾天回來看，也不會忘記當時為什麼這樣設計。

---

## 一、三層式文件結構（從上游概念到下游程式）

```mermaid
flowchart TD
    L1["🏛️ 第一層（最上游：全局地圖與共通語言）<br/>• docs/MAP.md（本檔案：子模組全景圖與職責切分）<br/>• docs/SYSTEM_BLUEPRINT.md（6大核心決策與官方規則邊界）"]
    L2["📐 第二層（中游：各模組細部規格書 — 供你與隊友討論維護）<br/>• docs/specs/M1_universe_spec.md（150檔名單、供應鏈圖譜、30檔ETF基準）<br/>• docs/specs/M2_collectors_spec.md（DataPack解析、四階段資料源、本機投餵口）<br/>• docs/specs/M3_funnel_spec.md（異動雷達、供應鏈傳導篩選、S與O編號器）<br/>• docs/specs/M4_pm_agent_spec.md（MarketView研判、個股推論I、目標權重D）<br/>• docs/specs/M5_sandbox_spec.md（官方公式算Orders、8大鐵律驗算、回推迴圈）"]
    L3["⚙️ 第三層（最下游：實際執行程式碼）<br/>• src/etf_agent/*.py & scripts/"]

    L1 --> L2 --> L3
```

---

## 二、全系統子模組展開詳圖（Detailed Sub-module Architecture）

這張圖把原本的 5 個大盒子徹底拆開，標示出**每一個內部子模組、傳遞的資料結構、以及 LangGraph 的回推條件邊（Conditional Edge）**：

```mermaid
flowchart TB
    %% ==================== 外部與本機輸入 ====================
    subgraph INPUTS ["📥 外部與本機輸入源 (Raw Inputs)"]
        IN_DP["官方 DataPack.json<br/>• trade_date / as_of_date<br/>• account (cash, nav_tradable)<br/>• holdings[] (code, shares, close_price)<br/>• prev_day_orders / compliance"]
        IN_API["外部公開 API (Stage 1~3)<br/>• TWSE 上市收盤價量/法人 API<br/>• TPEx 上櫃收盤價量/法人 API<br/>• yfinance 美股 (SOX/NDX/TSM)<br/>• MOPS 月營收與財報"]
        IN_EXT["本機隊友投餵口 (inputs/extra_signals/)<br/>• 課堂 Lab 財報/NLP 統計訊號 CSV/JSON<br/>• 隊友自訂爬蟲結果 (含 url 與 authority)"]
    end

    %% ==================== 模組 1：底層知識庫 ====================
    subgraph M1 ["📦 模組 1：底層知識庫與靜態字典 (M1_universe)"]
        M1_WL["1.1 150檔白名單與市場路由表<br/>• code ➔ name<br/>• code ➔ market ('twse' 100檔 / 'tpex' 50檔)"]
        M1_SC["1.2 供應鏈因果圖譜 (Supply Chain Graph)<br/>• 5大產業聚落分類<br/>• 上游龍頭 (Leader) ➔ 下游上市櫃供應鏈 (Followers)"]
        M1_BM["1.3 30檔市售主動ETF基準表<br/>• 00980A~00410A 前15大持股權重向量"]
    end

    %% ==================== 模組 2：資料收集與標準化 ====================
    subgraph M2 ["🔌 模組 2：資料收集與快照層 (M2_collectors)"]
        M2_DP["2.1 DataPack 解析器<br/>• 檢查 missing ('prev_settlement' 阻斷保護)<br/>• 提取昨日庫存股數與超限天數追蹤"]
        M2_PR["2.2 雙市場行情收集器 (含備援)<br/>• 主線：TWSE / TPEx OpenAPI<br/>• 備援：yfinance (.TW / .TWO)"]
        M2_MA["2.3 總經與夜盤收集器<br/>• SOX, NDX, TSM ADR, 台指期漲跌幅"]
        M2_EX["2.4 外掛訊號讀取器 (Lab & 隊友插槽)<br/>• 讀取 extra_signals/ 並驗證欄位<br/>• 統一轉換為 AnalystCard 格式"]
        M2_SNAP["2.5 原始資料快照器<br/>• 寫入 runs/日期/source_snapshots/ 供抽檢對帳"]
    end

    %% ==================== 模組 3：漏斗篩選與事實編排 ====================
    subgraph M3 ["🔍 模組 3：全市場掃描漏斗與事實匯流排 (M3_funnel)"]
        M3_HOLD["3.1 既有持股強制保留器<br/>• 昨日 shares > 0 之持股 100% 必入列 (約24檔)<br/>• 標記是否接近 10%/25% 上限或達第5天寬限期"]
        M3_SCAN["3.2 全市場異動雷達 (Anomaly Scanner)<br/>• 掃描 150 檔價量突破、法人買超、營收/新聞事件"]
        M3_PROP["3.3 供應鏈因果傳導器 (Causal Propagator)<br/>• 當龍頭異動時，從 1.2 圖譜拉出『同鏈但滯後未漲』之上櫃/中小型股 (~10檔)"]
        M3_HUB["3.4 L0/L1 事實鎖定與連續編號器 (Evidence Hub)<br/>• 合併去重後產出 ~35 檔 Focus List<br/>• 自動編排 S1..Sn (sources, 最多50筆)<br/>• 自動編排 O1..On (observations, 鎖定純數字 values, 最多100筆)"]
    end

    %% ==================== 模組 4：LangGraph AI 決策核心 ====================
    subgraph M4 ["🧠 模組 4：AI 決策子圖 (M4_pm_agent — LangGraph Nodes)"]
        M4_MV["4.1 市場姿態研判節點 (Market View Node)<br/>• 讀總經 O ➔ 決定 regime (risk_on/neutral/risk_off)<br/>• 決定 stance 與 posture (increase/hold/reduce + 目標現金區間)"]
        M4_INF["4.2 個股因果推論節點 (Inference Node)<br/>• 針對 ~35 檔結合公開說明書理念與 O 事實<br/>• 產出 I1..In (含 logic 與反方證據 counter_evidence, 最多60筆)"]
        M4_DEC["4.3 投組權重配置節點 (Portfolio Decision Node)<br/>• 對異動股產出 D1..Dn (BUY/ADD/TRIM/SELL_ALL + target_weight + funding_for)<br/>• 對未異動之既有持股 100% 產出 no_trade_decisions (附 reason_refs)"]
    end

    %% ==================== 模組 5：沙盒驗算與自我回推守門員 ====================
    subgraph M5 ["🛡️ 模組 5：沙盒試算與合規守門員 (M5_sandbox — Verifier Node)"]
        M5_ORD["5.1 官方委託公式推導器 (Order Calculator)<br/>• target_shares = floor(target_weight * nav / close_price / 1000) * 1000<br/>• diff = target_shares - current_shares ➔ 產出 L4 orders"]
        M5_CHK["5.2 八大鐵律驗算引擎 (8-Point Validator)<br/>① JSON Schema v4.3 格式與正則檢核<br/>② S←O←I←D←Order 引用鏈 0 斷環 & 流水號不跳號<br/>③ 昨日持股 100% 覆蓋於 decisions ⊕ no_trade_decisions<br/>④ 成交後持股檔數 20~30 檔 & 100% 在 150 檔白名單內<br/>⑤ 個股權重 (2330≤25%, 其他≤10%) & 賣出不超庫存<br/>⑥ 扣除手續費(0.1425%)+證交稅(0.3%)後預估現金落在 target_cash_pct_range 且 0%~25%<br/>⑦ 淨買賣流向符合 posture (increase/hold±2%/reduce) & funding_for 缺口真實存在<br/>⑧ Active Share 比對 30 檔 ETF 均 ≥ 15%"]
        M5_OUT["5.3 產出與審計黑盒子打包器 (Exporter)<br/>• D-Plan_TEAM_32_日期.json<br/>• human_summary.md (人類戰情表)<br/>• thought_trace.jsonl (Prompt/Token/版本號審計紀錄)"]
    end

    %% ==================== 連線關係 ====================
    IN_DP --> M2_DP
    IN_API --> M2_PR & M2_MA
    IN_EXT --> M2_EX
    M1_WL --> M2_PR & M3_HUB & M5_CHK
    M1_SC --> M3_PROP
    M1_BM --> M5_CHK

    M2_DP & M2_PR & M2_MA & M2_EX --> M2_SNAP
    M2_DP --> M3_HOLD
    M2_PR & M2_MA & M2_EX --> M3_SCAN
    M3_SCAN --> M3_PROP
    M3_HOLD & M3_SCAN & M3_PROP --> M3_HUB

    M3_HUB -->|"傳入鎖定之 S1..Sn, O1..On 與目前部位"| M4_MV
    M4_MV --> M4_INF --> M4_DEC
    M4_DEC -->|"提交決策草案"| M5_ORD
    M5_ORD --> M5_CHK
    M5_CHK -. "❌ 任一項未通過：附具體差額與錯誤報告 (Retry ≤ 3次)<br/>若達重試上限則啟動『數學安全微調保底』" .-> M4_DEC
    M5_CHK -->|"✅ 8 項全數通過"| M5_OUT
```

---

## 三、五個模組的上游規格書索引（點進去查看或與隊友討論各模組細節）

為了避免「討論完就忘記」，我們把上面 5 個大區塊各自獨立成一份詳細的規格書（放在 `docs/specs/` 下）：

| 模組代號 | 上游規格書連結 | 裡面記錄了什麼細節？ | 適合哪位隊友一起看？ |
| :--- | :--- | :--- | :--- |
| **M1** | [`docs/specs/M1_universe_spec.md`](specs/M1_universe_spec.md) | 150 檔上市櫃分類規則、5 大供應鏈上下游對照表、30 檔 ETF Active Share 比對方法 | 負責產業研究／選股分類的隊友 |
| **M2** | [`docs/specs/M2_collectors_spec.md`](specs/M2_collectors_spec.md) | `DataPack` 異常處理、TWSE/TPEx/美股抓取欄位、課堂 Lab 與隊友爬蟲的 `AnalystCard` 投餵格式 | 負責寫財報腳本／新聞爬蟲／指標的隊友 |
| **M3** | [`docs/specs/M3_funnel_spec.md`](specs/M3_funnel_spec.md) | Day 1 與 Day 2~24 如何從 150 檔篩出 ~35 檔、異動雷達公式、`S` 與 `O` 的無幻覺編號規則 | 負責量化篩選／事件觸發條件的隊友 |
| **M4** | [`docs/specs/M4_pm_agent_spec.md`](specs/M4_pm_agent_spec.md) | LangGraph State 欄位定義、`market_view` 姿態邏輯、個股推論 Prompt 結構、權重階梯分配原則 | 你（架構師）與負責調整 AI 提示詞的隊友 |
| **M5** | [`docs/specs/M5_sandbox_spec.md`](specs/M5_sandbox_spec.md) | 官方委託股數公式、手續費與證交稅計算、8 大檢核項目的數學式、回推重試與保底降級機制 | 你（架構師，系統防呆最後防線） |
