"""pytest_shim.py — 无 pytest 环境的测试兼容层。

用法（从项目任意位置）：
    python3 .agents/skills/agent-os-dev/scripts/pytest_shim.py tests/test_groups.py

实现的 pytest 特性：monkeypatch、tmp_path、pytest.raises、pytest.approx、
类风格（setup_method）与函数风格测试。项目测试不使用其他 pytest 特性。
ROOT 取本脚本所在目录的上一级（即项目根），从任意 cwd 可运行。
"""
import importlib
import importlib.util
import inspect
import json
import os
import sys
import tempfile
import traceback
import types
from pathlib import Path

def _find_root():
    """从 cwd 与脚本位置向上查找含 src/interfaces.py 的项目根。"""
    bases = [Path.cwd(), *Path.cwd().parents,
             Path(__file__).resolve(), *Path(__file__).resolve().parents]
    for base in bases:
        if (base / "src" / "interfaces.py").exists():
            return base
    return Path.cwd()


ROOT = _find_root()
sys.path.insert(0, str(ROOT))


class MonkeyPatch:
    """pytest.monkeypatch 最小子集：setattr（含字符串点路径）、delenv。"""

    def __init__(self):
        self._undo = []

    def setattr(self, target, name=None, value=None):
        if isinstance(target, str):  # 字符串形式: setattr("module.attr", value)
            mod_path, _, attr = target.rpartition(".")
            target = importlib.import_module(mod_path)
            name, value = attr, name
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def delenv(self, name, raising=True):
        if name in os.environ:
            self._undo.append((os.environ, name, os.environ[name]))
            del os.environ[name]
        elif raising:
            raise KeyError(name)

    def undo(self):
        for target, name, old in reversed(self._undo):
            setattr(target, name, old)
        self._undo.clear()


class _RaisesCtx:
    """pytest.raises：断言抛出指定异常，可选 match 子串。"""

    def __init__(self, exc, match=None):
        self.exc, self.match = exc, match

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is None or not issubclass(et, self.exc):
            return False
        if self.match and self.match not in str(ev):
            raise AssertionError(f"match {self.match!r} 不在异常信息 {str(ev)!r} 中")
        self.value = ev
        return True


class _Approx:
    """pytest.approx：相对/绝对容差比较。"""

    def __init__(self, expected, rel=None, abs=None):
        self.expected, self.rel, self.abs = expected, rel, abs

    def __eq__(self, other):
        tol = max(self.rel * abs(self.expected) if self.rel else 0.0,
                  self.abs if self.abs is not None else 0.0,
                  1e-9)
        return abs(other - self.expected) <= tol


def _fake_pytest_module():
    mod = types.ModuleType("pytest")
    mod.raises = _RaisesCtx
    mod.approx = _Approx
    return mod


def _fixtures(func):
    """按形参名注入 fixture。"""
    params = inspect.signature(func).parameters
    kwargs = {}
    if "monkeypatch" in params:
        kwargs["monkeypatch"] = MonkeyPatch()
    if "tmp_path" in params:
        kwargs["tmp_path"] = Path(tempfile.mkdtemp(prefix="pytest_tmp_"))
    return kwargs


def run_item(label, func, kwargs):
    mp = kwargs.get("monkeypatch")
    try:
        func(**kwargs)
        print(f"  PASS {label}")
        return True
    except Exception:
        print(f"  FAIL {label}")
        traceback.print_exc()
        return False
    finally:
        if mp:
            mp.undo()


def main(path):
    sys.modules.setdefault("pytest", _fake_pytest_module())
    spec = importlib.util.spec_from_file_location("t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    passed = failed = 0
    for name, obj in list(vars(mod).items()):
        if name.startswith("Test") and isinstance(obj, type):
            for mname in sorted(dir(obj)):
                if not mname.startswith("test_"):
                    continue
                inst = obj()
                setup = getattr(inst, "setup_method", None) or getattr(inst, "setUp", None)
                if setup:
                    setup()
                method = getattr(inst, mname)
                kwargs = _fixtures(method)
                if run_item(f"{name}.{mname}", method, kwargs):
                    passed += 1
                else:
                    failed += 1
                teardown = getattr(inst, "teardown_method", None) or getattr(inst, "tearDown", None)
                if teardown:
                    teardown()
        elif name.startswith("test_") and callable(obj):
            if run_item(name, obj, _fixtures(obj)):
                passed += 1
            else:
                failed += 1

    print(f"\n结果: {passed} 通过, {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(sys.argv[1])
