from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Dict


def coerce_metadata_dict(value: Any) -> Dict[str, Any]:
    """返回字典，无法解析的值返回空字典。

    除映射本身外，同时接受 JSON 对象字符串：A-Memorix 的 metadata 在 SQLite 中以
    JSON 文本列存储，检索期直接读取原始列时会拿到字符串。若不在此处解析，调用方
    会静默退化为空字典，使按 metadata 判定的范围过滤（如聊天流范围）把所有段落
    判为未知范围并被丢弃，最终表现为检索恒为空。
    """
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (str, bytes, bytearray)):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}
