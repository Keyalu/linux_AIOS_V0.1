"""safe_io.py — 统一安全落盘（优化方案 3.3：脱敏从"逐点补"下沉为"出口收口"）。

审计 #12 的教训：脱敏挂在每个持久化调用点，出口越多裸奔面越大。本模块把
"写 JSON / 追加 JSONL"收口成两个函数，并强制约定：
- mask 回调（通常是 SecuritySandbox.mask_record）**在函数内部**统一执行，
  调用方只交裸数据 —— 忘记传 mask 不再等于"凭据外流"，而是显式报错；
- 写临时文件 + os.replace 原子替换（与 stats.py 同款），崩溃不留半截 JSON；
- 失败返回 False（如实降级），由调用方决定是否留痕，绝不静默炸主流程。
"""

from __future__ import annotations

import json
import os
import tempfile


def _mask_or_raise(obj, mask):
    if mask is None:
        raise ValueError("safe_io 必须传 mask 回调（红线 15：一切入库出口过脱敏）")
    try:
        return mask(obj)
    except Exception:
        return obj          # 脱敏实现自身异常时原样写（与既有 audit() 口径一致）


def write_json(path: str, obj, mask, *, indent: int = 2) -> bool:
    """脱敏后原子写入一份 JSON。成功 True；IO 失败 False（不抛）。"""
    data = _mask_or_raise(obj, mask)
    try:
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=indent)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False


def append_jsonl(path: str, record, mask) -> bool:
    """脱敏后向 JSONL 追加一行。成功 True；IO 失败 False（不抛）。"""
    data = _mask_or_raise(record, mask)
    try:
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read_json(path: str, default=None):
    """读一份 JSON；不存在/坏损返回 default（产物回读的统一入口）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default
