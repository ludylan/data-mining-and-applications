# M5 沙盒試算與合規守門員規格書 (`M5_sandbox_spec.md`)

> **上游位置**：對應 [`docs/MAP.md`](../MAP.md) 中的 **模組 5 (`M5_sandbox`)**  
> **對應下游程式**：`src/etf_agent/sandbox.py`

---

## 1. 模組職責與邊界
這是整條管線的**最後一道鐵閘（Gatekeeper）**。任何來自 M4 AI 大腦的決策草案，都必須經過這個模組的**官方公式機械換算**與**八大鐵律驗算**。只要有一條沒過，就產生具體的差額報告打回給 M4 重調（或啟動數學自癒對齊），**保證最終寫入硬碟的 `D-Plan.json` 100% 零違規、零退件！**

---

## 2. 內部子模組詳細規格

### 子模組 5.1：官方委託公式推導器 (`derive_orders`)
嚴格依照《D-Plan 撰寫指南》§⑥ 演算法，從 `decisions` 機械推導 `orders`（絕不手調）：
1. 對每筆 `decision`（含 `ticker`, `action`, `target_weight`, `decision_id`）：
   * 若 `action == "SELL_ALL"`：`target_shares = 0`。
   * 其餘情況：
     $$\text{target\_shares} = \lfloor \frac{\text{target\_weight} \times \text{nav\_tradable}}{\text{close\_price} \times 1000} \rfloor \times 1000$$
2. 計算委託差額：
   $$\Delta \text{shares} = \text{target\_shares} - \text{current\_shares}$$
   * 若 $\Delta \text{shares} > 0$：產出 `{"ticker": ..., "side": "BUY", "shares": Δshares, "decision_ref": decision_id}`。
   * 若 $\Delta \text{shares} < 0$：產出 `{"ticker": ..., "side": "SELL", "shares": abs(Δshares), "decision_ref": decision_id}`（並強制確保 `abs(Δshares) <= current_shares` 不超賣）。
   * 若 $\Delta \text{shares} == 0$（例如目標權重微調不足 1 張 1000 股）：**注意！** 若該筆 `decision` 算出來 0 股委託，為了避免「有異動決策卻無對應委託單」，沙盒會自動將該檔移回 `no_trade_decisions` 並重編 `D1..Dk` 流水號！

### 子模組 5.2：八大鐵律與數學一致性驗算器 (`verify_dplan`)
逐項檢查並回傳 `(passed: bool, violation_messages: list[str])`：

| 檢核編號 | 檢核項目 | 數學與邏輯條件 |
| :--- | :--- | :--- |
| **Check 1** | **JSON Schema v4.3** | 所有必填欄位、型別、字串長度（如 `logic` $\ge 20$ 字）、時間格式 `+08:00`、`additionalProperties: false` 完全符合。 |
| **Check 2** | **引用鏈與連續編號** | `S1..Sn`, `O1..On`, `I1..In`, `D1..Dk` 均從 1 連續遞增無跳號；所有 `_ref` / `_refs` 指向的 ID 100% 存在。 |
| **Check 3** | **昨日持股 100% 覆蓋** | 昨日 `shares > 0` 之所有 `code`，聯集剛好等於 `{d.ticker for d in decisions if d.action != 'BUY'} ∪ {n.ticker for n in no_trade_decisions}`，且兩邊交集為空。 |
| **Check 4** | **檔數與白名單** | 預估成交後 `shares > 0` 之總檔數 $N \in [20, 30]$，且所有代號 $\in \text{UNIVERSE\_150}$。 |
| **Check 5** | **個股權重與庫存上限** | `2330` 預估權重 $\le 0.25$，其餘個股 $\le 0.10$；所有 `SELL` 股數 $\le$ 昨日庫存 `shares`。 |
| **Check 6** | **現金區間與交易成本** | 買進總額 $B = \sum (\text{BUY shares} \times P_{t-1})$，賣出總額 $S = \sum (\text{SELL shares} \times P_{t-1})$。<br>手續費與稅 $C = (B + S) \times 0.001425 + S \times 0.003$。<br>預估現金 $Cash_{est} = Cash_{t-1} - B + S - C \ge 0$，且預估現金比 $r_{cash} = Cash_{est} / \text{NAV}_{t-1} \in [0, 0.25]$，同時落在 `market_view.posture.target_cash_pct_range` 內。 |
| **Check 7** | **Posture 淨流向與 `funding_for`** | • 若 `increase`：$B > S$<br>• 若 `reduce`：$S > B$<br>• 若 `hold`：$|B - S| \le 0.02 \times \text{NAV}_{t-1}$<br>• 若有標註 `funding_for`：驗算「拿掉標註賣出後之現金比」確實低於 `target_cash_pct_range[0]`（若現金本來就夠，則自動移除多餘的 `funding_for` 標記以防退件）。 |
| **Check 8** | **Active Share $\ge 15\%$** | 呼叫 M1 比對 30 檔主動型 ETF，確保 $\min(\text{Active Share}) \ge 15\%$。 |

### 子模組 5.3：回推迴圈與數學對齊保底 (`self_correction_and_align`)
* 若 M4 提出的草案在 Check 6 或 Check 7 有微幅算術偏差（例如因 1000 股取整導致實際現金比為 `14.2%`，但 AI 宣告 `[0.10, 0.14]`），沙盒除了可退回給 M4 重調（最多 2 次）外，亦提供**「Posture 精確對齊模式」**，將 `orders` 實算出的淨流向與現金比自動校準回 `posture` 區間（並預留 $\pm 3\%$ 緩衝），確保 100% 通過伺服器驗算。
