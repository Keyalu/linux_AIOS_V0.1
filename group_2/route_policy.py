"""route_policy.py — 动作通道决策表 + 计划图消费分析。

背景：LLM 在通道导向的动作表里选 action，语义近邻组（web_search/mcp_search、
open_url/web_open）之间会猜错——提示词散文无法穷举这类歧义（实战三例：
搜索"湖南大学"落 mcp_search、点击 dock 落 open_app、open_url 拿关键词当网址）。

本模块把"通道选择"从猜测改为确定性推导，在规划完成后对步骤做改写。
判据全部机械可判定，不依赖 LLM：

1. 计划图消费分析：本步输出被后续步骤引用（{{prev_result}}/{{stepN.result}}）
   → 必须选"可产出数据"的通道（如 mcp_search）；
2. 无消费证据 → 选"可观测"通道（如 web_search，用户能看到结果页）；
3. 参数形态：open_url 的 url 不像网址 → 实为搜索关键词 → web_search；
4. 后续家族：之后有 web_click/web_state/web_extract（都在**自动化浏览器**里
   取页）→ 需在自动化浏览器里打开（open_url → web_open），否则点击无处着力、
   抽取拿到的是空白/陈旧页面。

改写留 route_reason（步骤级）+ 汇总进 plan["routes"]（审计）；新动作加入
语义近邻组时必须同步这里的决策表（见 recipes 配方 7）。
"""

from __future__ import annotations

import re

# 语义近邻歧义组：组内动作互为通道替代，改写只发生在组内
_SEARCH_GROUP = ("web_search", "mcp_search", "search")
# 修复逻辑审计 #23：web_extract 与 web_click/web_state 同族 —— 它读的是
# **自动化浏览器当前页**，不在 _WEB_INTERACT 家族里时，
# [open_url(http) → web_extract] 不改 web_open，抽取落在未打开的通道上必空。
_WEB_INTERACT = ("web_click", "web_state", "web_extract")
_PAGE_CONSUMERS = _WEB_INTERACT              # 消费方要的是"页面"而非数据


def consumed_by(steps: list[dict]) -> dict[int, str]:
    """输出被引用的 step_id → 引用者动作名（消费分析升级版）。

    消费方动作决定通道语义：web_click 引用搜索 → 要的是"结果页"；
    write_file/llm_answer 引用搜索 → 要的是"数据"。"""
    out: dict[int, str] = {}
    for idx, s in enumerate(steps):
        for ref in _refs_in(s.get("params")):
            if ref == "prev_result":
                if idx > 0:
                    out[steps[idx - 1]["step_id"]] = s.get("action", "")
                continue
            m = re.fullmatch(r"step(\d+)(?:\.result)?", ref)
            if m:
                out[int(m.group(1))] = s.get("action", "")
    return out


def _refs_in(value) -> set[str]:
    """递归收集参数里所有 {{引用}} 标记（str/dict/list 全覆盖）。

    兼容单闭括号残缺形态 {{prev_result}——提示词示例曾长期带此错。"""
    out: set[str] = set()
    if isinstance(value, str):
        out.update(m.strip()
                   for m in re.findall(r"\{\{([^{}]+?)\}?\}", value))
    elif isinstance(value, dict):
        for v in value.values():
            out |= _refs_in(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out |= _refs_in(v)
    return out


def consumed_outputs(steps: list[dict]) -> set[int]:
    """输出被后续步骤引用的 step_id 集合（consumed_by 的键集合视图）。"""
    return set(consumed_by(steps))


def _looks_like_url(s: str) -> bool:
    s = (s or "").strip()
    if re.match(r"^https?://", s, re.I):
        return True
    # 裸域名（www.baidu.com）也当网址；中文关键词/短语不会有"点分段+无空格"形态
    return bool(re.match(r"^[\w-]+(\.[\w-]+)+(/[^\s]*)?$", s)) and " " not in s


def _has_web_interact_after(steps: list[dict], idx: int) -> bool:
    return any(steps[j].get("action") in _WEB_INTERACT
               for j in range(idx + 1, len(steps)))


def _required_ok(action: str, params: dict,
                 schemas: dict | None) -> bool:
    """改写后目标动作的必需参数是否齐备（缺参则不改写，宁缺毋滥）。

    无 schema 目录时**不允许改写**：早期返回 True，等于在没有能力清单的
    情况下也敢换动作，可能换成一个根本不存在的工具。
    """
    if not schemas:
        return False
    schema = schemas.get(action)
    if not schema:
        return False                          # 目标动作不在能力清单里 → 不改写
    required = schema.get("inputSchema", {}).get("required", [])
    return all(params.get(k) not in ("", None) for k in required)


def apply_route_policy(intent_text: str, steps: list[dict],
                       schemas: dict | None = None,
                       channel_stats: dict | None = None) -> list[dict]:
    """对规划步骤做通道改写（原位修改），返回改写记录供 plan["routes"] 审计。

    channel_stats（优化方案 2.1，数据注入）：src.experience.channel_stats 的
    产物 {action: {n, fail_rate,...}}。**只注记不改道** —— 改写目标仍由上方
    硬规则决定（红线 3），经验只往 route_reason 追加"近期失败率"警示，
    让确认面板/事件流如实呈现该通道的历史表现。
    """
    consumed = consumed_by(steps)
    routes: list[dict] = []
    for idx, s in enumerate(steps):
        action = s.get("action")
        params = s.setdefault("params", {})
        target = s.get("target") or ""
        new_action = reason = None

        if action in _SEARCH_GROUP:
            consumer = consumed.get(s.get("step_id"), "")
            if consumer in _PAGE_CONSUMERS:
                # 消费方要的是"页面"：web_click 要在结果页上点，必须开浏览器
                if action != "web_search":
                    new_action = "web_search"
                    reason = f"后续 {consumer} 需要搜索页 → 改为浏览器结果页"
            elif consumer:
                if action != "mcp_search":
                    new_action = "mcp_search"
                    reason = f"后续 {consumer} 需要数据 → 改为数据通道 mcp_search"
            else:
                if action != "web_search":
                    new_action = "web_search"
                    reason = "输出无下游消费 → 改为可观测的浏览器结果页"
            if new_action and new_action != action:
                params.pop("engine", None)    # mcp_search 的 engine 语义不同
                params.setdefault("query", target)
        elif action == "open_url":
            url = str(params.get("url") or target or "").strip()
            if url and not _looks_like_url(url):
                new_action = "web_search"
                reason = "open_url 目标不是网址，实为搜索关键词 → web_search"
                params["query"] = url
                params.pop("url", None)
            elif url and _has_web_interact_after(steps, idx):
                new_action = "web_open"
                reason = "后续有网页交互步骤 → 需自动化浏览器承接，open_url 改 web_open"
        elif action == "web_open":
            url = str(params.get("url") or "").strip()
            if url and not url.lower().startswith(("http://", "https://")) \
                    and _looks_like_url(url):
                params["url"] = "https://" + url   # 裸域名补协议（不做会报参数错误）

        # 改写成 web_open 的同样要补协议（此前只对"原本就是 web_open"的步骤
        # 生效，[open_url(裸域名)→web_extract] 场景评测现场抓到）
        if new_action == "web_open":
            params["url"] = str(params.get("url") or target or "").strip()
            if params["url"] and not params["url"].lower().startswith(
                    ("http://", "https://")):
                params["url"] = "https://" + params["url"]

        if new_action and new_action != action:
            if _required_ok(new_action, params, schemas):
                s["action"] = new_action
                s["route_reason"] = reason
                if schemas and new_action in schemas:
                    s["requires_admin"] = \
                        schemas[new_action].get("permission") == "admin"
                routes.append({"step_id": s.get("step_id"), "from": action,
                               "to": new_action, "reason": reason})
            else:
                s["route_reason"] = \
                    f"未改写 {action}→{new_action}：目标动作缺少必需参数"

        # 经验警示注记（优化方案 2.1）：样本足够且失败率高才提示；不改道
        if channel_stats:
            watched = s.get("action")
            st = channel_stats.get(watched) or {}
            if st.get("n", 0) >= 3 and st.get("fail_rate", 0) >= 0.5:
                warn = (f"⚠ 经验警示：近 {st['n']} 次 {watched} 通道"
                        f"失败率 {round(st['fail_rate'] * 100)}%")
                s["route_reason"] = (f"{s['route_reason']}；{warn}"
                                     if s.get("route_reason") else warn)
                s["route_warning"] = True
    return routes
