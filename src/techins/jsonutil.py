from __future__ import annotations

import json
from typing import Any

import numpy as np


def _default(o: Any) -> Any:
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def jsonable(obj: Any) -> Any:
    """把 numpy 等类型转换为可写入 JSON 列的原生类型。"""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=_default))
