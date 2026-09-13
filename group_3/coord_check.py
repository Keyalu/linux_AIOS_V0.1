"""coord_check.py — AT-SPI extents 与 X 端真值的交叉校正（方案二）。

实测根因（2026-09 本机）：GTK4 应用在 X11 下 a11y extents 停留在窗口
创建时的默认几何，窗口被移动/缩放后不刷新 —— Nautilus 报 (0,0,890,550)
而 X 真值是 1012x672+317+88（非等比，纯陈旧数据），bbox 中心点击系统性
偏移 100+ 像素；对照 gnome-shell（gjs 应用）树坐标与 X root 完全一致。

校正策略（宁缺毋滥，任何一步拿不准就原样返回）：
- 以元素所属顶层 frame 为锚：frame 的 AT-SPI 几何 vs xwininfo 真值，
  逐轴仿射校正 true = a*reported + b 套用到元素 bbox（实测误差非等比，
  纯平移不够）；
- gnome-shell 自身树直接信任（实测准，且合成鼠标事件对它本就不可靠）；
- Wayland / 无 DISPLAY / 无 xwininfo → 跳过（Wayland 下 extents 与
  shell 同处逻辑坐标系，本就自洽）；
- 匹配不上 X 窗口、缩放因子超出界、或偏差在容差内 → 不校正。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

# AT-SPI 对不可见控件的位置哨兵值
SENTINEL = -2147483648
# 仿射因子安全界：超出视为窗口匹配错误，宁可不动
_MIN_SCALE, _MAX_SCALE = 0.4, 2.5
# 平移/缩放容差：以内视为一致，不做无谓校正
_TOL_PX, _TOL_SCALE = 4.0, 0.03
_XWININFO_TTL = 0.5                       # 秒；VMware autofit 改分辨率窗口期可忽略


def enabled() -> bool:
    """校正开关：X11 会话 + 有 xwininfo 才启用（AIOS_COORD_FIX=0 强制关）。"""
    if os.environ.get("AIOS_COORD_FIX") == "0":
        return False
    if not os.environ.get("DISPLAY"):
        return False
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return False
    return shutil.which("xwininfo") is not None


def parse_xwininfo(text: str) -> list[dict]:
    """解析 xwininfo -root -tree 输出 → [{"name", "x", "y", "w", "h"}]。

    只认带标题的行（"(has no name)" 无引号，自然跳过）。"""
    wins: list[dict] = []
    pat = re.compile(r'^\s*(0x[0-9a-fA-F]+)\s+"([^"]*)":\s*\([^)]*\)\s+'
                     r'(-?\d+)x(-?\d+)\+(-?\d+)\+(-?\d+)')
    for line in (text or "").splitlines():
        m = pat.match(line)
        if m:
            wins.append({"name": m.group(2),
                         "w": int(m.group(3)), "h": int(m.group(4)),
                         "x": int(m.group(5)), "y": int(m.group(6))})
    return wins


def match_window(name: str, wins: list[dict]) -> dict | None:
    """按标题匹配 X 窗口：精确优先，其次双向子串；同名取面积最大者。

    1x1（隐藏占位）窗口不参与匹配。"""
    if not name or not wins:
        return None
    cands = [w for w in wins if w["w"] > 2 and w["h"] > 2]
    exact = [w for w in cands if w["name"] == name]
    if not exact:
        exact = [w for w in cands if name in w["name"] or w["name"] in name]
    return max(exact, key=lambda w: w["w"] * w["h"]) if exact else None


def apply_affine(bbox: list, reported: list,
                 true: list) -> tuple[list, dict]:
    """逐轴仿射校正（纯函数，便于单测）：true = a*reported + b。

    返回 (校正后 bbox, info)；容差内/因子离谱时原样返回并说明原因。"""
    info = {"coord_fix": False,
            "frame_reported": reported, "frame_true": true}
    rw, rh = reported[2], reported[3]
    if rw <= 0 or rh <= 0:
        info["reason"] = "degenerate-frame"
        return bbox, info
    ax, ay = true[2] / rw, true[3] / rh
    if not (_MIN_SCALE <= ax <= _MAX_SCALE and _MIN_SCALE <= ay <= _MAX_SCALE):
        info["reason"] = "suspicious-scale"
        return bbox, info
    bx, by = true[0] - ax * reported[0], true[1] - ay * reported[1]
    if (abs(bx) <= _TOL_PX and abs(by) <= _TOL_PX
            and abs(ax - 1) <= _TOL_SCALE and abs(ay - 1) <= _TOL_SCALE):
        info["reason"] = "within-tolerance"
        return bbox, info
    out = [round(ax * bbox[0] + bx), round(ay * bbox[1] + by),
           max(1, round(ax * bbox[2])), max(1, round(ay * bbox[3]))]
    info.update({"coord_fix": True,
                 "scale": [round(ax, 3), round(ay, 3)],
                 "offset": [round(bx), round(by)]})
    return out, info


_cache_ts, _cache_wins = 0.0, []


def x_windows(timeout: float = 2.0) -> list[dict]:
    """当前全部顶层 X 窗口几何（短 TTL 缓存；失败返回空 = 不校正）。"""
    global _cache_ts, _cache_wins
    now = time.monotonic()
    if now - _cache_ts < _XWININFO_TTL:
        return _cache_wins
    try:
        r = subprocess.run(["xwininfo", "-root", "-tree"],
                           capture_output=True, text=True, timeout=timeout)
        wins = parse_xwininfo(r.stdout) if r.returncode == 0 else []
    except Exception:
        wins = []
    _cache_ts, _cache_wins = now, wins
    return wins


def frame_of(acc, max_up: int = 40):
    """沿父链上溯顶层窗口 frame：返回 (frame_acc, app_name)。

    acc 本身就是 frame 时以其为锚（父链止于 application，取自己）。"""
    cur, frame, app_name = acc, None, ""
    try:
        if cur.get_role_name() == "frame":
            frame = cur
    except Exception:
        pass
    for _ in range(max_up):
        try:
            parent = cur.get_parent()
            if parent is None:
                break
            role = parent.get_role_name()
            if role in ("application", "desktop", "desktop frame"):
                app_name = parent.get_name() or ""
                break
            if role == "frame":
                frame = parent
            cur = parent
        except Exception:
            break
    return frame, app_name


def corrected(acc, bbox) -> tuple[list, dict]:
    """校正元素 bbox：acc 是元素的可访问对象（可为 None → 不校正）。

    哨兵/畸形 bbox 原样放行 —— 有效性判定归 automator 的点击路径。"""
    info: dict = {"coord_fix": False}
    raw = list(bbox) if isinstance(bbox, (list, tuple)) and len(bbox) >= 4 \
        else []
    if not raw or acc is None or not enabled():
        return raw, info
    frame, app_name = frame_of(acc)
    if frame is None:
        info["reason"] = "no-frame-ancestor"
        return raw, info
    if "gnome-shell" in (app_name or "").lower():
        info["reason"] = "shell-tree-trusted"
        return raw, info
    try:
        e = frame.get_extents(1)
        reported = [int(e.x), int(e.y), int(e.width), int(e.height)]
    except Exception:
        info["reason"] = "extents-unavailable"
        return raw, info
    if reported[2] <= 0 or reported[3] <= 0:
        info["reason"] = "degenerate-frame"
        return raw, info
    true_w = match_window(frame.get_name() or "", x_windows())
    if true_w is None:
        info["reason"] = "x-window-unmatched"
        return raw, info
    true = [true_w["x"], true_w["y"], true_w["w"], true_w["h"]]
    return apply_affine(raw, reported, true)
