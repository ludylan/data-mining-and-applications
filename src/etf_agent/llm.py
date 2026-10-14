"""取得 LLM 的统一入口。节点要用模型时,一律呼叫 `get_llm("角色名")`。

- 每个角色用哪个模型,写在专案根目录的 `config.toml`,不用改程式。
- 没有设定 API key 时回传 None,节点应退回 stub 行为 → 没有 key 也能跑完整条流程。
- API key 放在 `.env`(参考 `.env.example`),不要提交到 GitHub。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_CONFIG = Path(__file__).resolve().parents[2] / "config.toml"


def get_llm(role: str):
    cfg = tomllib.loads(_CONFIG.read_text(encoding="utf-8")) if _CONFIG.exists() else {}
    models = cfg.get("models", {})
    model_name = models.get(role, models.get("default"))
    if not model_name or not os.getenv("GOOGLE_API_KEY"):
        return None  # 没设定 → 使用 stub
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model_name)
