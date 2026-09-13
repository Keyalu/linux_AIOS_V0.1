"""test_integration_v10.py — Agent_OS_v1.0 五组集成联调测试。

覆盖任务书演示场景表（GUI 场景走 Mock 后端）+ 契约形状 + 安全拦截 + RAG。
运行：python tests/test_integration_v10.py（或用项目根的兼容层跑）。
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main as system_main                     # noqa: E402
from group_5 import RAGKnowledgeBase           # noqa: E402
from group_3 import coord_check                # noqa: E402
from group_3.automator import _valid_bbox      # noqa: E402
from group_3.atspi_io import AtspiIO           # noqa: E402
from group_2.task_planner import TaskPlanner   # noqa: E402
from group_1.host_agent import HostAgent       # noqa: E402
import base64 as _b64                          # noqa: E402
import hashlib as _hashlib                     # noqa: E402
import shutil as _shutil                       # noqa: E402
import socket as _socket                       # noqa: E402
import struct as _struct                       # noqa: E402
import threading as _threading                 # noqa: E402
from src.web_automation import (               # noqa: E402
    _WebSocket, web_click as _web_click, web_open as _web_open,
    web_state as _web_state, register_web_tools,
)
from src.tool_registry import ToolRegistry     # noqa: E402


class IntegrationBase(unittest.TestCase):
    def setUp(self):
        self.out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                f".tmp_out_{id(self)}")
        self.coordinator = system_main.build(out=self.out)

    def tearDown(self):
        shutil_cleanup(self.out)


def shutil_cleanup(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


class TestScenarios(IntegrationBase):

    def test_s1_open_app_gui_steps(self):
        b = self.coordinator.orchestrate("打开文件管理器")
        kinds = [s["kind"] for s in b["results"]]
        self.assertEqual(kinds[0], "gui")
        self.assertTrue(all(s["tool_result"]["success"] for s in b["results"]))
        self.assertEqual(b["audit"]["verdict"], "PASS")

    def test_s2_navigate_path(self):
        b = self.coordinator.orchestrate("导航到 /home/user/Documents")
        types = [s for s in b["results"] if s["name"] == "type"]
        self.assertTrue(types and "/home/user/Documents" in
                        json.dumps(types[0]["params_sent"]))

    def test_s3_create_folder(self):
        b = self.coordinator.orchestrate("创建 project 文件夹")
        self.assertTrue(any(s["name"] == "hotkey" or
                            s.get("params_sent", {}).get("text") == "project"
                            for s in b["results"]))
        self.assertEqual(b["audit"]["verdict"], "PASS")

    def test_s5_switch_window(self):
        b = self.coordinator.orchestrate("切换到浏览器")
        self.assertEqual(b["results"][0]["name"], "hotkey")

    def test_s6_organize_real_tools(self):
        b = self.coordinator.orchestrate(
            "帮我把「demo_task」文件夹里的文件整理一下，顺便看看有没有重复文件，最后备个份")
        self.assertEqual(b["session"]["summary"]["fail"], 0)
        self.assertEqual(b["audit"]["verdict"], "PASS")
        self.assertTrue(os.path.exists(os.path.join(self.out, "demo_task_backup.zip")))

    def test_weather_query(self):
        b = self.coordinator.orchestrate("查询长沙天气")
        step = b["results"][0]
        self.assertEqual(step["name"], "mcp_weather")
        self.assertEqual(step["params_sent"].get("city"), "长沙")
        self.assertIn("长沙", json.dumps(step["tool_result"]["result"],
                                         ensure_ascii=False))


class TestSecurityAndContract(IntegrationBase):

    def test_dangerous_rejected_before_planning(self):
        b = self.coordinator.orchestrate("rm -rf /")
        self.assertFalse(b["check"]["approved"])
        self.assertEqual(b["check"]["risk_level"], "critical")
        self.assertEqual(b["results"], [])
        self.assertIsNone(b.get("plan"))   # 拦截发生在规划层之前

    def test_check_json_contract_shape(self):
        b = self.coordinator.orchestrate("查询长沙天气")
        self.assertEqual({"approved", "risk_level", "reason", "layer"},
                         set(b["check"]))

    def test_email_draft_mode_dry_run(self):
        b = self.coordinator.orchestrate("起草一封邮件给a@b.com，主题是hi，先别发送只预览")
        step = b["results"][0]
        self.assertEqual(step["name"], "send_email")
        self.assertTrue(step["params_sent"].get("dry_run"))  # 起草语义 → 不真发

    def test_email_explicit_send_semantics(self):
        it = __import__("group_1").HostAgent().understand_intent(
            "给a@b.com发一封邮件，主题是hi")
        self.assertFalse(it["params"]["dry_run"])  # 明确发送 → 真发语义（安全由提权流把关）


class TestPersistence(IntegrationBase):

    def test_artifacts_persisted(self):
        self.coordinator.orchestrate("查询长沙天气")
        for name in ("intent.json", "plan.json", "session_log.json",
                     "check.json", "audit.json", "stats.json",
                     "rag_traces.jsonl", "audit_log.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(self.out, name)), name)

    def test_rag_retrieval_after_runs(self):
        self.coordinator.orchestrate("查询长沙天气")
        self.coordinator.orchestrate("帮我把「demo_task」整理一下")
        hits = self.coordinator.rag.query("查天气")
        self.assertTrue(hits and "天气" in hits[0]["input"])


class TestClickAccuracy(unittest.TestCase):
    """方案二+三：AT-SPI 坐标校正、哨兵判定、语义选动作（纯函数单测）。"""

    # 本机实测：GTK4 应用 X11 下 a11y extents 停留在窗口默认几何
    MEASURED_REPORTED = [0, 0, 890, 550]
    MEASURED_TRUE = [317, 88, 1012, 672]

    def test_apply_affine_measured_case(self):
        out, info = coord_check.apply_affine([10, 200, 50, 30],
                                             self.MEASURED_REPORTED,
                                             self.MEASURED_TRUE)
        self.assertTrue(info["coord_fix"])
        self.assertEqual(out, [328, 332, 57, 37])   # 逐轴仿射，非纯平移

    def test_apply_affine_within_tolerance_noop(self):
        out, info = coord_check.apply_affine([5, 5, 10, 10],
                                             [0, 0, 100, 100],
                                             [0, 0, 100, 100])
        self.assertEqual(out, [5, 5, 10, 10])
        self.assertFalse(info["coord_fix"])

    def test_apply_affine_refuses_suspicious_scale(self):
        out, info = coord_check.apply_affine([5, 5, 10, 10],
                                             [0, 0, 100, 100],
                                             [0, 0, 500, 500])
        self.assertEqual(out, [5, 5, 10, 10])
        self.assertEqual(info["reason"], "suspicious-scale")

    def test_apply_affine_degenerate_frame(self):
        out, info = coord_check.apply_affine([5, 5, 10, 10],
                                             [0, 0, 0, 100],
                                             [0, 0, 100, 100])
        self.assertEqual(out, [5, 5, 10, 10])
        self.assertEqual(info["reason"], "degenerate-frame")

    def test_parse_xwininfo(self):
        sample = ('     0x2403d9e "AIOS_V0.1": ("org.gnome.Nautilus" '
                  '"org.gnome.Nautilus")  1012x672+317+88  +317+88\n'
                  '        0x3800004 (has no name): ()  1x1+-1+-1  +-1+-1\n'
                  '     0x1234 "Term": ("xterm" "xterm")  800x600+-99+-99\n')
        wins = coord_check.parse_xwininfo(sample)
        self.assertEqual(wins, [
            {"name": "AIOS_V0.1", "w": 1012, "h": 672, "x": 317, "y": 88},
            {"name": "Term", "w": 800, "h": 600, "x": -99, "y": -99},
        ])

    def test_match_window_prefers_visible_largest(self):
        wins = [{"name": "AIOS_V0.1", "w": 1, "h": 1, "x": 0, "y": 0},
                {"name": "AIOS_V0.1", "w": 1012, "h": 672, "x": 317, "y": 88}]
        hit = coord_check.match_window("AIOS_V0.1", wins)
        self.assertEqual((hit["w"], hit["h"]), (1012, 672))
        self.assertIsNone(coord_check.match_window("不存在", wins))
        self.assertIsNone(coord_check.match_window("", wins))

    def test_valid_bbox_sentinel_and_negative_coords(self):
        self.assertFalse(_valid_bbox(None))
        self.assertFalse(_valid_bbox([0, 0, 10, 10][:2]))
        self.assertFalse(_valid_bbox([-2147483648, -2147483648, 10, 10]))
        self.assertFalse(_valid_bbox([0, 0, 1, 1]))      # 1x1 占位
        self.assertTrue(_valid_bbox([-100, -50, 10, 10]))  # 多显示器合法负坐标
        self.assertTrue(_valid_bbox([317, 88, 1012, 672]))

    def test_action_index_semantic_priority(self):
        class FakeAcc:
            def __init__(self, names):
                self._names = names
            def get_n_actions(self):
                return len(self._names)
            def get_action_name(self, i):
                return self._names[i]

        # "press" 优先于盲选 index 0（那里可能是 showContextMenu）
        self.assertEqual(AtspiIO.action_index(
            FakeAcc(["showContextMenu", "press"])), 1)
        # 多个动作无一匹配点击语义 → 不猜，交回坐标路径
        self.assertIsNone(AtspiIO.action_index(
            FakeAcc(["showContextMenu", "focus"])))
        # 唯一动作即使名字陌生也用它
        self.assertEqual(AtspiIO.action_index(FakeAcc(["toggle"])), 0)
        self.assertEqual(AtspiIO.action_index(FakeAcc(["openMenu"])), 0)
        # 动作接口异常 → None
        class BadAcc:
            def get_n_actions(self):
                raise RuntimeError("dead")
        self.assertIsNone(AtspiIO.action_index(BadAcc()))


class TestIntentPathNormalization(IntegrationBase):
    """组1 归一回归（实战事故驱动）：规则引擎曾把 organize path='.' 原样
    放行，整个项目根被分类移动；相对 dest 让 zip 落进进程 CWD；绝对但
    不存在的 dest 被回退改写成源目录。三类都必须归一到工作区内。"""

    DEMO_TEXT = ("帮我把「demo_task」文件夹里的文件整理一下，"
                 "顺便看看有没有重复文件，最后备个份")

    def test_dir_scope_paths_confined_to_workspace(self):
        host = self.coordinator.modules[1]
        host.use_llm = False                 # 确定性：规则引擎路径
        b = self.coordinator.orchestrate(self.DEMO_TEXT)
        self.assertEqual(b["session"]["summary"]["fail"], 0)
        for rec in b["results"]:
            p = rec.get("params_sent") or {}
            for k in ("path", "src", "directory", "dest"):
                v = p.get(k)
                if v is not None:
                    self.assertTrue(os.path.isabs(str(v)),
                                    f"{rec['name']}.{k}={v!r} 仍相对/越界")
                    self.assertTrue(str(v).startswith(self.out),
                                    f"{rec['name']}.{k}={v!r} 落在工作区外")
        # 备份产物落位正确（曾因 dest 失真整个断言失败）
        self.assertTrue(os.path.exists(
            os.path.join(self.out, "demo_task_backup.zip")))

    def test_normalize_dest_unit(self):
        from group_1 import HostAgent
        base = "/tmp/aios_norm_dest"
        os.makedirs(base, exist_ok=True)     # 父目录必须真实（输出路径语义）
        src = os.path.join(base, "demo_task")
        # 相对 dest → 锚定 src 同级，不落 CWD
        self.assertEqual(HostAgent._normalize_dest("demo_task_backup",
                                                   src, src),
                         os.path.join(base, "demo_task_backup"))
        # 绝对 dest 父目录真实 → 接受（输出允许尚不存在）
        self.assertEqual(HostAgent._normalize_dest(
            os.path.join(base, "new_backup"), src, src),
            os.path.join(base, "new_backup"))
        # 幻觉绝对 dest（父目录不存在）→ 丢弃，交由组2 重派生
        self.assertIsNone(HostAgent._normalize_dest(
            "/no/such/parent_xyz/backup", src, src))


class TestWebAutomation(unittest.TestCase):
    """CDP 网页通道：WS 帧回路（本地假服务器）、Mock 降级、组4 注册。"""

    # ---- 本地假 WebSocket 服务器：验证握手/掩码帧/收发回路 ----
    def _fake_ws_roundtrip(self, payload="hello-web"):
        """起一个一次性 WS 服务器：收到客户端掩码文本帧 → 回显文本帧。"""
        srv = _socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        done = {}

        def serve():
            conn, _ = srv.accept()
            try:
                buf = b""
                while b"\r\n\r\n" not in buf:
                    buf += conn.recv(4096)
                head, rest = buf.split(b"\r\n\r\n", 1)
                key = ""
                for line in head.split(b"\r\n"):
                    if line.lower().startswith(b"sec-websocket-key:"):
                        key = line.split(b":", 1)[1].strip().decode()
                accept = _b64.b64encode(_hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
                ).digest()).decode()
                conn.sendall((f"HTTP/1.1 101 Switching Protocols\r\n"
                              f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                              f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                              ).encode())
                # 读一帧客户端掩码文本
                b = rest
                while len(b) < 2:
                    b += conn.recv(4096)
                ln = b[1] & 0x7F
                pos = 2
                if ln == 126:
                    while len(b) < 4:
                        b += conn.recv(4096)
                    ln = _struct.unpack(">H", b[2:4])[0]
                    pos = 4
                mask = b[pos:pos + 4]
                need = pos + 4 + ln
                while len(b) < need:
                    b += conn.recv(4096)
                data = bytes(c ^ mask[i % 4]
                             for i, c in enumerate(b[pos + 4:need]))
                done["received"] = data.decode()
                # 回一帧未掩码文本（服务端→客户端不掩码；支持扩展长度）
                d = payload.encode()
                if len(d) < 126:
                    frame = bytes([0x81, len(d)])
                elif len(d) < 65536:
                    frame = bytes([0x81, 126]) + _struct.pack(">H", len(d))
                else:
                    frame = bytes([0x81, 127]) + _struct.pack(">Q", len(d))
                conn.sendall(frame + d)
            finally:
                conn.close()
                srv.close()

        th = _threading.Thread(target=serve, daemon=True)
        th.start()
        ws = _WebSocket(f"ws://127.0.0.1:{port}/devtools/page/test")
        ws.send_text("hello-web")
        got = ws.recv_text()
        th.join(timeout=5)
        self.assertEqual(done.get("received"), "hello-web")
        self.assertEqual(got, payload)
        ws.close()

    def test_ws_client_roundtrip(self):
        self._fake_ws_roundtrip("hello-web")
        big = "x" * 70000                       # 16 位扩展长度帧
        self._fake_ws_roundtrip(big)

    def test_web_tools_mock_degrade(self):
        """浏览器不可用 → [Mock] 标记 + simulated=True（任务书降级要求）。"""
        import src.web_automation as _wa
        old = os.environ.get("AIOS_WEB_BROWSER")
        old_port = os.environ.get("AIOS_CDP_PORT")
        os.environ["AIOS_WEB_BROWSER"] = "/nonexistent-browser-xyz"
        os.environ["AIOS_CDP_PORT"] = "59999"
        try:
            r = _web_open("https://example.com")
            self.assertTrue(r["success"])
            self.assertTrue(r.get("simulated"))
            self.assertIn("[Mock]", str(r["result"]))
            r = _web_click(text="xx")
            self.assertTrue(r.get("simulated"))
        finally:
            if old is None:
                os.environ.pop("AIOS_WEB_BROWSER", None)
            else:
                os.environ["AIOS_WEB_BROWSER"] = old
            if old_port is None:
                os.environ.pop("AIOS_CDP_PORT", None)
            else:
                os.environ["AIOS_CDP_PORT"] = old_port
            # 单例缓存了 59999 端口会污染后续测试 —— 复位
            _wa._AUTO = None

    def test_web_tools_registered(self):
        reg = ToolRegistry()
        register_web_tools(reg)
        names = {s.to_dict()["name"] for s in reg.list_tools()}
        self.assertTrue({"web_open", "web_click", "web_state"} <= names)
        # web_click 参数防御：text/selector 全空 → 报参数错误而非崩溃
        r = _web_click()
        self.assertFalse(r["success"])
        self.assertIn("text / selector", r["error"])


class TestPlannerRouting(unittest.TestCase):
    """组2 路由：dock 类点击 → 启动通道；普通控件照常 click。"""

    def setUp(self):
        self.planner = TaskPlanner()

    def _plan_click(self, name):
        intent = {"intent": "GUI 操作", "target": name,
                  "goals": [{"action": "click_element",
                             "params": {"name": name}}]}
        return self.planner.plan(intent)

    def test_dock_icon_click_routes_to_open_app(self):
        plan = self._plan_click("应用中心")
        self.assertEqual(plan["steps"][0]["action"], "open_app")
        self.assertEqual(plan["steps"][0]["params"]["app"], "应用中心")

    def test_long_description_not_routed(self):
        plan = self._plan_click("我的文件报告")
        self.assertEqual(plan["steps"][0]["action"], "click")

    def test_rules_web_click_no_double_goal(self):
        host = HostAgent(use_llm=False)
        it = host.understand_intent("点击网页里的 Learn more")
        actions = [g["action"] for g in it.get("goals", [])]
        self.assertEqual(actions, ["web_click"])
        # 规则路径参数挂在 intent.params（goal 层由规划器合并）
        self.assertEqual(it["params"].get("text"), "Learn more")

    def test_rules_plain_click_single_goal(self):
        host = HostAgent(use_llm=False)
        it = host.understand_intent("点击 应用中心")
        actions = [g["action"] for g in it.get("goals", [])]
        self.assertEqual(actions, ["click_element"])


def _has_automation_browser() -> bool:
    return any(os.path.exists(p) or _shutil.which(p)
               for p in ("/snap/bin/chromium", "chromium-browser",
                         "chromium", "google-chrome"))


@unittest.skipUnless(_has_automation_browser(), "无 Chromium 系浏览器")
class TestWebCDPIntegration(unittest.TestCase):
    """真机 CDP 集成：open → click → 页面真实跳转（URL 验证）。"""

    def test_open_click_navigate(self):
        r1 = _web_open("https://example.com")
        self.assertTrue(r1["success"], r1)
        self.assertEqual(r1["result"]["title"], "Example Domain")
        r2 = _web_click(text="Learn more")
        self.assertTrue(r2["success"], r2)
        self.assertIn("iana.org", r2["result"]["after"]["url"])
        r3 = _web_state()
        self.assertIn("iana.org", r3["result"]["url"])


class TestDemoMatrix(unittest.TestCase):

    def test_full_scenario_table_passes(self):
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           f".tmp_demo_{id(self)}")
        try:
            rc = system_main.run_demo(out=out)
            if rc != 0:
                # 真实 LLM 在环存在输出波动，偶发变体意图允许重试一次
                rc = system_main.run_demo(out=out)
            self.assertEqual(rc, 0)
        finally:
            shutil_cleanup(out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
