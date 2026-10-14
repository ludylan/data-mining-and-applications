"""外挂注册机制:组员新增功能的唯一入口。

组员只要在 `observers/` 或 `retrievers/` 资料夹新增一个 .py 档,
用 `@observer("名字")` 或 `@retriever("名字")` 装饰函式,系统会自动发现并执行。
不需要改 graph.py,也不需要懂整张图。

某个外挂出错时,只会被记录到 errors,不会拖垮整条流程。
"""

from __future__ import annotations

import importlib
import pkgutil
import traceback
from collections.abc import Callable

# kind -> {name: function}
_REGISTRY: dict[str, dict[str, Callable]] = {"observer": {}, "retriever": {}}
_PACKAGES = {"observer": "etf_agent.observers", "retriever": "etf_agent.retrievers"}


def observer(name: str):
    """登记一个「观察」外挂。函式签名: fn(as_of: str) -> list[Evidence]"""
    return _register("observer", name)


def retriever(name: str):
    """登记一个「提取」外挂。函式签名: fn(as_of: str, store) -> list[dict]"""
    return _register("retriever", name)


def _register(kind: str, name: str):
    def deco(fn: Callable) -> Callable:
        _REGISTRY[kind][name] = fn
        return fn

    return deco


def discover(kind: str) -> dict[str, Callable]:
    """自动 import 资料夹内所有模组,让装饰器完成登记。"""
    pkg = importlib.import_module(_PACKAGES[kind])
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith("_"):  # 底线开头的档案视为停用/草稿
            continue
        importlib.import_module(f"{_PACKAGES[kind]}.{m.name}")
    return dict(_REGISTRY[kind])


def run_all(kind: str, *args, **kwargs) -> tuple[list, list[str]]:
    """执行某一类的所有外挂。回传 (结果合并清单, 友善错误讯息清单)。"""
    results: list = []
    errors: list[str] = []
    for name, fn in discover(kind).items():
        try:
            out = fn(*args, **kwargs) or []
            results.extend(out)
        except Exception as exc:  # noqa: BLE001 - 刻意宽松,隔离单一外挂失败
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            errors.append(
                f"[{kind}:{name}] 执行失败 → {type(exc).__name__}: {exc} "
                f"(位置: {tb.filename}:{tb.lineno})。其他外挂不受影响。"
            )
    return results, errors
