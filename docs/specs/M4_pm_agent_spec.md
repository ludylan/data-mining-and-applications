# M4 AI 決策大腦子圖規格書 (`M4_pm_agent_spec.md`)

> **上游位置**：對應 [`docs/MAP.md`](../MAP.md) 中的 **模組 4 (`M4_pm_agent`)**  
> **對應下游程式**：`src/etf_agent/pm_agent.py`

---

## 1. 模組職責與邊界
這是系統中唯一呼叫 LLM（支援 Gemini / Groq 多金鑰切換與無 Key 規則保底）的決策核心。
它接收 M3 編好號的 `sources (S)`、`observations (O)` 以及目前持股狀態，負責產出：
1. **L2M `market_view`**（大盤走向、風險姿態與目標現金區間）。
2. **L2 `inferences (I1..In)`**（個股因果推論與反方證據 `counter_evidence`）。
3. **L3 `decisions (D1..Dn)` 與 `no_trade_decisions`**（目標權重配置與續抱理由）。

---

## 2. 內部子模組（LangGraph 節點）詳細規格

### 子模組 4.1：大盤姿態研判節點 (`market_view_node`)
* **輸入**：總經級事實（`O1..Om`）、目前帳戶現金比例（`cash_ratio_tradable`）。
* **決定邏輯**：
  * 判斷 `regime`：`risk_on`（偏多進攻）/ `neutral`（震盪整理）/ `risk_off`（偏空防禦）。
  * 判斷 `stance`：`aggressive` / `neutral` / `defensive`。
  * 決定 `posture`：
    * **Day 1 首日建倉**：目前現金 100%，必須大舉建倉，故 `net_exposure_intent` 固定為 `"increase"`，`target_cash_pct_range` 設為 `[0.10, 0.20]`（目標留約 15% 現金）。
    * **Day 2~24 日常調倉**：依 `regime` 與換股計畫決定是 `"increase"`、`"hold"`（淨買賣差 $\le 2\%$ NAV）或 `"reduce"`，並設定安全現金區間（如 `[0.08, 0.18]`）。
  * 撰寫 `logic`（20～1000 字，呼應公開說明書理念）與 `counter_evidence`（反方風險訊號）。

### 子模組 4.2：個股因果推論節點 (`inferences_node`)
* **輸入**：Focus List 個股之 `observations`、所屬供應鏈聚落（來自 M1）、公開說明書理念。
* **輸出（`I1..In`，連續編號，$\le 60$ 筆）**：
  * 為每一檔「現有持股」與「決定買進之新候選股」撰寫至少 1 筆推論 `I`。
  * **`premise_refs`**：綁定該個股及同供應鏈龍頭之 `obs_id`（例如上櫃設備股同時引用台積電法說 `O` 與自身價量 `O`，展現上下游傳導邏輯！）。
  * **`logic`**：20～1000 字，說明為何從上述事實推導出加碼、減碼、新建倉或續抱。
  * **`counter_evidence`**：必填（字串或 `null`，建議每筆皆填寫具體潛在風險以拿高分）。

### 子模組 4.3：投組權重配置與修正節點 (`decisions_node`)
* **輸入**：`inferences (I1..In)`、現有持股、以及（若是回推重試）**來自 M5 沙盒的錯誤修正報告**。
* **權重分配與防撞衫護欄（Active Share Defense）**：
  1. **總持股檔數**：控制在 **22 ～ 26 檔**（安全落在 20～30 檔正中央）。
  2. **上市櫃混血比例**：確保上櫃（`tpex`）與非前 30 大中小型股合計權重佔 **35% ～ 45%**。
  3. **單一權重上限**：`2330` 控制於 `0.12 ~ 0.20`（嚴格 $\le 0.25$），其餘核心股 `0.04 ~ 0.06`，衛星股 `0.02 ~ 0.035`（嚴格 $\le 0.10$）。
  4. **100% 持股覆蓋**：
     * 有異動的股票 ➔ 放入 `decisions`（`BUY` / `ADD` / `TRIM` / `SELL_ALL`），編號 `D1..Dk`。
     * 未異動的既有持股 ➔ 100% 放入 `no_trade_decisions`（附上對應的 `reason_refs: ["I..."]`）。
  5. **資金調度 (`funding_for`)**：若當日有賣出（`TRIM`/`SELL_ALL`）且不賣會跌破現金下限，則標註 `funding_for` 指向當日買進之 `D` ID。
