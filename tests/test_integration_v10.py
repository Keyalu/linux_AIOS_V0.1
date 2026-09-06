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
