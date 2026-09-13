"""
mcp_connector.py — 模型上下文协议集成模块

融合了三个版本的优点：
- 我搭建版本的 MCP Server 连接管理、Mock 工具注册
- 版本2 的 JSON-RPC 2.0 Mock 调用格式
- 真实 MCP 接入预留接口
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime
from typing import Any, Callable
from urllib.parse import quote
from .interfaces import (
    IToolRegistry, ToolResult, ToolSchema, ToolParam, ToolParamType,
)

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def _http_get(url: str, timeout: int = 8) -> bytes:
    """组4 零依赖 HTTP GET（真实 MCP 数据源共用）。"""
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# WMO 天气代码 → 中文描述
_WMO = {0: "晴", 1: "基本晴", 2: "局部多云", 3: "阴", 45: "雾", 48: "雾凇",
        51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 61: "小雨", 63: "中雨",
        65: "大雨", 66: "冻雨", 67: "强冻雨", 71: "小雪", 73: "中雪", 75: "大雪",
        77: "米雪", 80: "阵雨", 81: "强阵雨", 82: "暴雨", 85: "阵雪", 86: "强阵雪",
        95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹"}


def _real_weather(city: str, days: int) -> str | None:
    """真实天气：Open-Meteo 地理编码 + 预报（免费无 Key）。失败返回 None。"""
    geo = json.loads(_http_get(
        f"https://geocoding-api.open-meteo.com/v1/search?name={quote(city)}"
        f"&count=1&language=zh"))
    results = geo.get("results") or []
    if not results:
        return None
    r0 = results[0]
    days = max(1, min(int(days), 7))
    w = json.loads(_http_get(
        f"https://api.open-meteo.com/v1/forecast?latitude={r0['latitude']}"
        f"&longitude={r0['longitude']}&current=temperature_2m,weather_code"
        f"&daily=temperature_2m_max,temperature_2m_min,weather_code"
        f"&timezone=auto&forecast_days={days}"))
    cur = w.get("current", {})
    daily = w.get("daily", {})
    lines = [f"{r0.get('name', city)} 当前 {cur.get('temperature_2m', '?')}°C，"
             f"{_WMO.get(cur.get('weather_code'), '未知')}"]
    for i, day in enumerate(daily.get("time", [])):
        code = (daily.get("weather_code") or [None] * len(daily["time"]))[i]
        lo = daily.get("temperature_2m_min", ["?"] * len(daily["time"]))[i]
        hi = daily.get("temperature_2m_max", ["?"] * len(daily["time"]))[i]
        lines.append(f"{day}: {_WMO.get(code, '未知')} {lo}~{hi}°C")
    return "\n".join(lines)


def _real_search(query: str) -> str | None:
    """真实搜索：搜狗网页结果解析（免 Key）。失败返回 None。"""
    html = _http_get(f"https://www.sogou.com/web?query={quote(query)}").decode("utf-8", "ignore")
    blocks = re.findall(
        r'<h3[^>]*class="[^"]*vr-?title[^"]*"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        html, re.S | re.I)
        # 逐条剥掉 HTML 标签并去重，最多取前 5 条
    lines = []
    for u, t in blocks:
        title = re.sub(r"<[^>]+>|<!--.*?-->", "", t).strip()
        if not title or title in [l.split(" ", 1)[-1] for l in lines]:
            continue
        url = "https://www.sogou.com" + u if u.startswith("/link") else u
        lines.append(f"• {title}\n  {url[:90]}")
        if len(lines) >= 5:
            break
    return "\n".join(lines) if lines else None


def _real_translate(text: str, to_lang: str) -> str | None:
    """真实翻译：MyMemory 免费接口（免 Key）。失败返回 None。"""
    pair = "zh-CN|en" if to_lang.lower().startswith("en") else "en|zh-CN"
    data = json.loads(_http_get(
        f"https://api.mymemory.translated.net/get?q={quote(text)}"
        f"&langpair={pair}"))
    translated = (data.get("responseData") or {}).get("translatedText", "")
    if (data.get("responseStatus") == 200 and translated
            and "INVALID" not in translated.upper()
            and "MYMEMORY WARNING" not in translated.upper()):
        return translated
    return None


class MCPConnector:
    """MCP 协议连接器。当前为 Mock 实现，预留真实接入接口。"""

    def __init__(self) -> None:
        self.servers: dict[str, dict] = {}
        self.tools: dict[str, Callable] = {}

    # ----------------------------------------------------------
    # MCP Server 管理
    # ----------------------------------------------------------

    def connect_server(self, name: str, config: dict) -> dict:
        """连接一个 MCP Server（当前为 Mock）。"""
        if name in self.servers:
            return {"success": False, "error": f"服务器 '{name}' 已连接"}

        self.servers[name] = {
            "name": name,
            "config": config,
            "status": "connected",
            "tools_count": 0,
        }
        return {"success": True, "server_id": name}

    def disconnect_server(self, name: str) -> dict:
        """断开 MCP Server 连接。"""
        if name not in self.servers:
            return {"success": False, "error": f"服务器 '{name}' 未连接"}
        del self.servers[name]
        return {"success": True, "result": f"已断开: {name}"}

    def list_servers(self) -> list[dict]:
        """列出所有已连接的 MCP Server。"""
        return list(self.servers.values())

    # ----------------------------------------------------------
    # Mock 工具注册
    # ----------------------------------------------------------

    def register_mock_tools(self, registry: IToolRegistry) -> None:
        """将 Mock MCP 工具注册到 ToolRegistry（幂等：重复调用不会报错）。"""
        self._setup_mock_search(registry)
        self._setup_mock_weather(registry)
        self._setup_mock_translate(registry)

        # 在服务器清单里登记一个 Mock Server（GUI 连接管理页可见）
        server_id = "mock-server"
        self.servers[server_id] = {
            "name": server_id,
            "config": {"mode": "mock"},
            "status": "connected",
            "tools_count": 3,
        }

    def _setup_mock_search(self, registry: IToolRegistry) -> None:
        def mock_search(query: str, engine: str = "web") -> str:
            try:
                real = _real_search(query)
                if real:
                    return f"[真实·搜狗] {query}\n{real}"
            except Exception:
                pass
            return f"[Mock·离线] 搜索 ({engine}): 找到 3 条 {query} 相关结果"
        # Week3 加固：已注册则跳过，保证重复调用 register_mock_tools 不崩溃
        if not registry.has("mcp_search"):
            registry.register("mcp_search", mock_search, ToolSchema(
                name="mcp_search",
                description="联网搜索（真实·搜狗；网络不可用时降级 Mock）",
                parameters=[
                    ToolParam("query", ToolParamType.STRING, "搜索关键词"),
                    ToolParam("engine", ToolParamType.STRING, "搜索引擎",
                              required=False, default="web"),
                ],
            ))

    def _setup_mock_weather(self, registry: IToolRegistry) -> None:
        def mock_weather(city: str, days: int = 1) -> str:
            try:
                real = _real_weather(city, days)
                if real:
                    return f"[真实·Open-Meteo] {real}"
            except Exception:
                pass
            return "[Mock·离线] 天气: {city} 晴转多云, 18°C~28°C, 空气质量良".format(city=city)
        if not registry.has("mcp_weather"):
            registry.register("mcp_weather", mock_weather, ToolSchema(
                name="mcp_weather",
                description="查询城市天气（真实·Open-Meteo；网络不可用时降级 Mock）",
                parameters=[
                    ToolParam("city", ToolParamType.STRING, "城市名"),
                    ToolParam("days", ToolParamType.NUMBER, "预报天数",
                              required=False, default=1),
                ],
            ))

    def _setup_mock_translate(self, registry: IToolRegistry) -> None:
        def mock_translate(text: str, to_lang: str = "en") -> str:
            try:
                real = _real_translate(text, to_lang)
                if real:
                    return f"[真实·MyMemory] {real}"
            except Exception:
                pass
            samples = {
                ("你好", "en"): "Hello",
                ("Hello", "zh"): "你好",
            }
            return samples.get((text, to_lang), f"[Mock·离线] {text} -> {to_lang}")
        if not registry.has("mcp_translate"):
            registry.register("mcp_translate", mock_translate, ToolSchema(
                name="mcp_translate",
                description="文本翻译（真实·MyMemory；网络不可用时降级 Mock）",
                parameters=[
                    ToolParam("text", ToolParamType.STRING, "要翻译的文本"),
                    ToolParam("to_lang", ToolParamType.STRING, "目标语言",
                              required=False, default="en"),
                ],
            ))

    # ----------------------------------------------------------
    # JSON-RPC 2.0 Mock 调用（来自版本2）
    # ----------------------------------------------------------

    def mock_jsonrpc_call(
        self,
        registry: IToolRegistry,
        name: str,
        params: dict[str, Any] | None = None,
    ) -> dict:
        """以 JSON-RPC 2.0 格式调用工具（Mock）。

        错误码细分（Week3 加固，符合 JSON-RPC 2.0 规范）：
        -32601  Method not found  —— 工具未注册
        -32603  Internal error    —— 工具执行失败
        """
        params = params or {}
        # 用时间哈希造一个伪请求 id（JSON-RPC 响应需回带 id）
        req_id = abs(hash(datetime.now().isoformat())) % 100000
        result = registry.call(name, params)
        if result.success:
            error = None
        elif not registry.has(name):
            error = {"code": -32601, "message": result.error}
        else:
            error = {"code": -32603, "message": result.error}
        return {
            "jsonrpc": "2.0",
            "result": result.result if result.success else None,
            "error": error,
            "id": req_id,
        }

    # ----------------------------------------------------------
    # 真实 MCP 接入预留
    # ----------------------------------------------------------

    def connect_real_mcp(self, command: str, args: list[str]) -> dict:
        """连接真实 MCP Server（预留接口）。"""
        return {
            "success": False,
            "error": (
                "真实 MCP 连接尚未实现。"
                "请安装 mcp SDK (pip install mcp) 后替换此 Mock。"
                f"预期配置: command={command}, args={args}"
            ),
        }
