# 脈絡圖 (Map)

> 這張圖是全隊的「地圖」。**改了流程，就要同步改這張圖**（規則見文末）。
> 圖用 Mermaid 寫，GitHub 與 Obsidian 都會自動渲染，改文字就是改圖。

## 一、主圖（永遠只有這幾個節點）

```mermaid
flowchart LR
    T(["① 觸發<br/>as_of + trigger"]) --> O["② 觀察 observe<br/>跑所有 observers/*.py"]
    O -->|寫入| M[("③ 記憶 memory<br/>證據庫 SQLite+FTS5")]
    M -->|讀取 受 as_of 限制| D
    subgraph D["④ 決策 decide (子圖)"]
        direction LR
        R["提取 retrieve<br/>跑所有 retrievers/*.py"] --> B["多方 bull"] --> E["空方 bear"] --> J["裁判 judge"] --> H["硬規則 enforce_rules"]
    end
    D --> REC["⑤ 記錄 record<br/>每日調倉報告"]
    REC -.-> RUNS[("runs/時間/<br/>每個節點輸出")]
```

## 二、組員的位置（「畫布」在哪裡）

```mermaid
flowchart TB
    subgraph 畫布["🎨 自由畫布：放進資料夾即可，不必改圖"]
        OB["observers/*.py<br/>新增觀察：爬蟲、財報、技術指標…"]
        RT["retrievers/*.py<br/>新增提取：決定哪些證據給 AI 看"]
    end
    subgraph 框架["🔒 框架：改之前先討論"]
        ST["state.py 共用狀態"]
        GR["graph.py 主圖接線"]
        DC["decision.py 決策子圖"]
        MM["memory.py 證據庫"]
        RL["rules.py 硬規則"]
    end
    OB --> MM
    MM --> RT
    RT --> DC
    ST -.契約.- GR
```

## 三、資料怎麼流動

| 步驟 | 誰負責 | 輸入 | 輸出 |
|---|---|---|---|
| 觸發 | 排程 / 手動 | — | `as_of`（截至時間）、`trigger`（僅記錄） |
| 觀察 | `observers/` 外掛 | `as_of` | `Evidence` 證據條目 → 寫入證據庫 |
| 記憶 | `memory.py` | Evidence | 三層內容：概要 → 要點 → 原文 |
| 提取 | `retrievers/` 外掛 | `as_of` + 證據庫 | 概要層證據清單 |
| 決策 | `decision.py` | 證據清單 | 多空論述 → 調倉 (trades) |
| 硬規則 | `rules.py` | 調倉 | 被鎖住的調倉（如每日上限 25 次） |
| 記錄 | `graph.py: record` | 全部 | `runs/<時間>/report.md` |

## 四、未來擴充位置（尚未實作，先占位）

```mermaid
flowchart LR
    X1["數值線：技術指標 / 機器學習<br/>→ 包裝成數據工具，供 Agent 查詢"]
    X2["因果白板 Agent (子圖)<br/>新證據進來時維護因果鏈"]
    X3["更多角色：風險官等<br/>→ 加在 decision.py 子圖"]
    X4["向量檢索<br/>→ 換 memory.py 內部，介面不變"]
```

## 五、如何維護這張圖（規則）

1. **改流程 = 改圖。** 動了 `graph.py` 或 `decision.py` 的節點或連線，同一個 PR 裡更新上面的 Mermaid。
2. **新增外掛不用改圖。** 放進 `observers/` 或 `retrievers/` 就好；若是很重要的新來源，在「三、資料怎麼流動」補一行說明。
3. **主圖保持精簡。** 新的複雜度一律做成**子圖**收進某個區塊，不要往主圖加節點。
4. **State 是契約。** 新增欄位要在 `state.py` 寫清楚用途與註解，並在 Discord 通知大家。
5. **圖與程式不一致時，以程式為準，然後立刻補圖。**
6. 寫 Mermaid 小提醒：含括號或特殊符號的文字請用雙引號包起來 `A["文字 (說明)"]`，換行用 `<br/>`。
