"""llm_client.py — 组内轻量 LLM 文本客户端（零 SDK，OpenAI 兼容接口）。

供 llm_answer 等"理解/生成"类工具使用；与组1 HostAgent 的意图客户端
相互独立：配置同源（system_out/llm_config.json，环境变量兜底），但
职责不同——这里只做"给一段文本、回一段文本"的通用调用。

配置解析优先级：system_out/llm_config.json（GUI 保存）→ 环境变量
DASHSCOPE_API_KEY / OPENAI_API_KEY + OPENAI_BASE_URL。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "system_out" / "llm_config.json"
_DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def load_config() -> dict:
    """解析当前可用的 LLM 配置；无 Key 时返回 {}（调用方诚实失败）。"""
    cfg: dict = {}
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        pass
    base = (cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL")
            or _DEFAULT_BASE).rstrip("/")
    model = cfg.get("model") or os.environ.get("AIOS_LLM_MODEL") or "qwen-plus"
    key = cfg.get("api_key") or os.environ.get("DASHSCOPE_API_KEY") \
        or os.environ.get("OPENAI_API_KEY") or ""
    if not key:
        return {}
    return {"base_url": base, "model": model, "api_key": key}


def chat_stream(messages: list, timeout: int = 120):
    """流式对话：按 OpenAI 兼容 SSE 逐段 yield 增量文本。

    messages：[{"role": "user"|"assistant"|"system", "content": str}]，
    由调用方维护历史（本模块无状态）。无配置时抛 RuntimeError；
    厂商流式异常由调用方捕获后可降级 chat_once。"""
    cfg = load_config()
    if not cfg:
        raise RuntimeError("未配置 LLM Key（LLM 设置页检查）")
    for m in messages[-16:]:
        m["content"] = str(m.get("content", ""))[:4000]
    body = json.dumps({"model": cfg["model"], "messages": messages[-16:],
                       "temperature": 0.3, "stream": True}).encode()
    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                chunk = json.loads(payload)
                delta = (chunk.get("choices") or [{}])[0].get(
                    "delta", {}).get("content")
            except json.JSONDecodeError:
                delta = None
            if delta:
                yield delta


def chat_once(messages: list, timeout: int = 60) -> str | None:
    """非流式对话（流式降级用）：返回完整回复文本，失败返回 None。"""
    cfg = load_config()
    if not cfg:
        return None
    msgs = [{"role": m.get("role", "user"),
             "content": str(m.get("content", ""))[:4000]}
            for m in messages[-16:]]
    body = json.dumps({"model": cfg["model"], "messages": msgs,
                       "temperature": 0.3}).encode()
    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"].strip()
        return re.sub(r"^```(json)?|```$", "", content, flags=re.M).strip() or None
    except Exception:
        return None


def chat(prompt: str, system: str = "", timeout: int = 60,
         max_input_chars: int = 8000) -> str | None:
    """发送一段文本给 LLM，返回回复文本；失败返回 None（调用方决定降级）。"""
    cfg = load_config()
    if not cfg:
        return None
    prompt = (prompt or "")[:max_input_chars]
    body = json.dumps({
        "model": cfg["model"],
        "messages": ([{"role": "system", "content": system}] if system else [])
                    + [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"].strip()
        # 剥掉 LLM 可能包裹的 markdown 代码栏
        return re.sub(r"^```(json)?|```$", "", content, flags=re.M).strip() or None
    except Exception:
        return None
