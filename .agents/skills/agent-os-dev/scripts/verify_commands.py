"""验证"可由自然语言触发的指令阶梯"（从 .verify_cmds.py 转正而来）：先看路由与计划，再真跑低风险的那几条。

只做两件事：
  1) 对每条候选指令打印：组1 解析出的动作链 / 组2 的步骤 / 是否需要提权 / 未解参数；
  2) 对其中不依赖桌面与浏览器的指令，走完整编排（组1→组5→组2→组3→组4←→组5），
     打印真实结果摘要（审计 verdict、步数、关键产出）。
不修改任何源码，产物落在 system_out/（正常运行时目录）。
"""
import json
import os
import sys

# 向上找到含 main.py 的项目根（不写死层数：脚本可能被挪到别处）
ROOT = os.path.dirname(os.path.abspath(__file__))
while ROOT != os.path.dirname(ROOT) and not os.path.isfile(
        os.path.join(ROOT, "main.py")):
    ROOT = os.path.dirname(ROOT)
sys.path.insert(0, ROOT)

import main as M

# 复杂度由低到高的候选阶梯（措辞必须真实命中规则/提示词）
CANDIDATES = [
    ("L1 只读技能", "查一下磁盘使用情况"),
    ("L2 安全拦截", "rm -rf /"),
    ("L3 写文件", "写一个文件 /tmp/aios_note.txt 内容是 hello"),
    ("L4 截屏", "截个屏保存到 /tmp/aios_shot.png"),
    ("L5 文件整理", "把「demo_task」文件夹里的文件整理一下"),
    ("L6 组合任务", "帮我把「demo_task」文件夹里的文件整理一下，顺便看看有没有重复文件，最后备个份"),
    ("L7 邮件草稿", "起草一封邮件发给 someone@example.com，主题是验收测试"),
    ("L8 网页搜索", "联网搜索湖南大学"),
    ("L9 提权命令", "执行命令「echo aios_verify」"),
]

# 可在本机（无桌面/无浏览器无妨）真跑的：其余只做路由校验
RUNNABLE = {"L1 只读技能", "L2 安全拦截", "L3 写文件", "L5 文件整理",
            "L6 组合任务", "L7 邮件草稿", "L9 提权命令"}


def main() -> int:
    out = os.path.join(ROOT, "system_out")
    coord = M.build(out)

    print("=" * 78)
    print("【一】路由与计划校验（组1 解析 + 组2 规划，不执行）")
    print("=" * 78)
    planned = {}
    for tag, text in CANDIDATES:
        it = coord.modules[1].understand_intent(text, default_target=coord.default_target)
        actions = [g.get("action") for g in it.get("goals", [])] or [it.get("action")]
        try:
            plan = coord.modules[2].plan(it)
            steps = [s["action"] for s in plan.get("steps", [])]
            admin = [s["step_id"] for s in plan.get("steps", []) if s.get("requires_admin")]
            unresolved = plan.get("unresolved", [])
        except Exception as e:
            steps, admin, unresolved = [f"<规划异常 {type(e).__name__}>"], [], []
        planned[tag] = (text, actions, steps, admin, unresolved)
        print(f"\n{tag}｜{text}")
        print(f"  组1 动作链 : {actions}")
        print(f"  组2 步骤   : {steps}   （提权步骤: {admin or '无'}）")
        if unresolved:
            print(f"  未解参数   : {unresolved}")

    print()
    print("=" * 78)
    print("【二】真跑（走完整编排：组1→组5安全→组2→组3→组4←→组5审计/RAG）")
    print("=" * 78)
    for tag, text in CANDIDATES:
        if tag not in RUNNABLE:
            print(f"\n{tag}｜{text}\n  跳过执行：依赖桌面会话/自动化浏览器，本机无法实测"
                  f"（在目标机上按同一命令跑即可）")
            continue
        b = coord.orchestrate(text)
        if b.get("rejected"):
            print(f"\n{tag}｜{text}\n  ⛔ 被安全层拦截：{b['rejected']}")
            continue
        a = b.get("audit", {})
        sess = b.get("session", {})
        s = sess.get("summary", {})
        print(f"\n{tag}｜{text}")
        print(f"  审计     : {a.get('verdict')}（{s.get('success')}/{s.get('total')} 步成功，"
              f"对账一致={a.get('consistent')}）")
        for st in sess.get("steps", []):
            tr = st.get("tool_result") or {}
            res = tr.get("result")
            brief = (json.dumps(res, ensure_ascii=False)[:96] if not isinstance(res, str)
                     else res[:96]).replace("\n", " ")
            print(f"    · 步骤{st['step']} {st['name']}（{st.get('level_used')}）"
                  f"{'成功' if tr.get('success') else '失败'} → {brief}")
        esc = sess.get("escalations") or []
        if esc:
            print(f"  提权     : {[(e['tool'], e.get('confirm_mode')) for e in esc]}")
        if sts := sess.get("self_healed"):
            print(f"  自愈     : {sts}")

    print("\n完成：以上即『能真跑并验证』的指令阶梯。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
