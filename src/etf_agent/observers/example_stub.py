"""示範「觀察」外掛：新增自己的觀察，只要複製這個檔案、改名、改內容。

規則：
1. 檔名隨意（不要用底線開頭，底線開頭會被當成停用）。
2. 用 @observer("唯一名字") 裝飾函式。
3. 函式收到 as_of，回傳一串 Evidence。可以回傳空清單。
4. 想暫時停用？把檔名改成底線開頭，例如 `_my_scraper.py`。
5. 出錯不會拖垮別人，錯誤會出現在當次報告的 errors 裡。

⚠ visible_at 要填「這條資訊真正能被看到的時間」，不能晚於 as_of 才看得到的資訊卻填成早的時間。
"""

from etf_agent.memory import Evidence
from etf_agent.plugins import observer


@observer("example_stub")
def example_stub(as_of: str) -> list[Evidence]:
    day = as_of[:10]
    return [
        Evidence(
            id=f"stub-{day}-001",
            source="stub://example",
            published_at=f"{day}T08:00:00",
            visible_at=f"{day}T08:00:00",
            summary="(示範)某公司財報營收低於市場預期",
            detail="營收年增 3%，市場預期 8%。",
            passage="……本季營收為 100 億元，較去年同期成長 3%……",
            stance="bear",
            tickers=["0050"],
            topics=["財報", "預期差"],
            context="示範 / 財報 / 營收",
        )
    ]
