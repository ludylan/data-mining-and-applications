# ETF Agent — AI CUP 2026 (Team 32)

事件導向、因果與趨勢追蹤的 ETF 基金經理人 Agent。使用 LangGraph 搭建。
目前是**草框**：整條流程已能跑通，但所有金融邏輯都是 stub，等大家逐步替換。

## 先看這三份
| 檔案 | 內容 |
|---|---|
| [docs/MAP.md](docs/MAP.md) | 脈絡圖：整個系統長什麼樣、我的位置在哪、怎麼維護這張圖 |
| [docs/PHILOSOPHY.md](docs/PHILOSOPHY.md) | 設計哲學：寫給組員，也可以直接貼給 AI 助手 |
| 本頁 | 怎麼啟動、怎麼新增自己的東西、財報處理腳本放哪裡 |

## 快速啟動
```powershell
# 1. 安裝 (需先裝 uv: https://docs.astral.sh/uv/)
uv sync

# 2. (可選) 設定 API key。沒有也能跑，會使用 stub
copy .env.example .env     # 然後編輯 .env 填入 GOOGLE_API_KEY

# 3. 跑一次流程
uv run python -m etf_agent --as-of 2026-01-05

# 4. 看結果：runs/ 裡最新的資料夾，有 report.md 和每個節點的輸出
# 5. 跑測試
uv run pytest
```

## 目錄結構
```
config.toml                各角色使用的模型 (改這裡換模型)
src/etf_agent/
  observers/               🎨 你的「觀察」放這裡 (爬蟲、財報處理…)
  retrievers/              🎨 你的「提取」放這裡 (哪些證據給 AI 看)
  graph.py                 🔒 主圖接線
  decision.py              🔒 決策子圖 (多方/空方/裁判/硬規則)
  state.py                 🔒 共用狀態 (契約)
  memory.py                🔒 證據庫
  rules.py                 🔒 硬規則
data/                      證據庫 (自動產生，不進 Git)
runs/                      每次執行的追溯記錄 (自動產生，不進 Git)
tests/                     測試
```
🎨 = 自由發揮，不必改圖。🔒 = 改之前先在 Discord 討論。

---

## 財報處理腳本應該放哪裡？（給負責財報的組員指引）

如果你正在開發財報解析、抓取或萃取的腳本，請依照以下方式整合：

### 1. 它是獨立腳本還是觀察外掛？
* **做法 A（最推薦，直接成為系統一部分）**：
  在 `src/etf_agent/observers/` 底下新增一個檔案，例如 `src/etf_agent/observers/earnings_report.py`。
  只要定義一個函式並加上 `@observer("earnings_report")` 裝飾器，系統就會在每次執行時自動調用它！
  
  ```python
  from etf_agent.memory import Evidence
  from etf_agent.plugins import observer

  @observer("earnings_report")
  def parse_earnings(as_of: str) -> list[Evidence]:
      # 1. 讀取或下載 as_of 之前的財報
      # 2. 進行關鍵字萃取、數據分析或 LLM 摘要
      # 3. 組裝成 Evidence 清單回傳 (三層結構: summary, detail, passage)
      return [
          Evidence(
              id=f"earnings-2330-{as_of[:10]}",
              source="公開資訊觀測站",
              published_at=f"{as_of[:10]}T09:00:00",
              visible_at=f"{as_of[:10]}T09:00:00", # 注意: 必須早於或等於 as_of
              summary="台積電 Q4 毛利率超越財測高標",
              detail="毛利率達 54.2%，單季 EPS 9.2 元。",
              passage="原始財報摘要段落...",
              stance="bull", # 偏多/偏空/不確定
              tickers=["2330", "0050"],
              topics=["財報", "毛利率"],
              context="2025Q4 財務報告書",
          )
      ]
  ```

* **做法 B（如果是前置處理、獨立工具或批次下載）**：
  若該腳本只需要離線批次執行（例如每週爬一次 PDF 放在本機），可以在專案根目錄下建立 `scripts/` 或 `tools/` 資料夾放獨立腳本。產生出來的整理後資料，再透過做法 A 的 `observers` 讀取並轉成 `Evidence` 寫入記憶庫。

---

## 我想新增一個「觀察」(例如爬某個網站)
1. 複製 `src/etf_agent/observers/example_stub.py`，改個名字 (不要用底線開頭)。
2. 改 `@observer("你的唯一名字")`，把函式內容換成你的邏輯，**回傳 `Evidence` 清單**。
3. 執行 `uv run python -m etf_agent --as-of 2026-01-05`。成功會在 `runs/` 看到新證據。
4. 搞壞了？不用擔心，錯誤只會出現在報告的「錯誤與警告」，不會影響別人。想暫停就把檔名改成底線開頭。

## 我想新增一個「提取」
同上，複製 `retrievers/recent_all.py`，用 `@retriever("名字")`，回傳 `store.search(...)` 的結果。

## 想用不同的模型？
編輯 `config.toml` 的 `[models]`，程式裡用 `get_llm("角色名")` 取得。

## 待團隊討論
- **證據的分群**：目前只有立場三類 (偏多/偏空/不確定) 加自由主題。歡迎提出更好的分法，可以讓 AI 助手給意見，再來改 `memory.py` 的 `STANCES`。
- 調倉時間點 (目前每日至少一份報告，上限 25 次調倉，時間待定)。
- 數值線 (技術指標/機器學習) 與因果白板 Agent 的設計。
- 記憶庫是否需要向量檢索。
