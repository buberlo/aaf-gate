import ast
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from framework import canon
from framework import gate
from framework.gatesocket import GateServer
from framework import hermes_adapter

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = REPO_ROOT / "adapters/hermes/aaf-gate/__init__.py"
YAML_PATH = REPO_ROOT / "adapters/hermes/aaf-gate/plugin.yaml"


def load_plugin():
    spec = importlib.util.spec_from_file_location("aaf_gate_plugin", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    def __init__(self):
        self.hooks = []

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))


class ConfigFakeCtx(FakeCtx):
    def __init__(self, socket_path=None, get_config_raises=None, label_ids=None):
        super().__init__()
        self._socket_path = socket_path
        self._get_config_raises = get_config_raises
        self._label_ids = label_ids

    def get_config(self, key, default=None):
        if self._get_config_raises is not None:
            raise self._get_config_raises
        if key == "socket_path":
            return self._socket_path if self._socket_path is not None else default
        if key == "label_ids":
            return self._label_ids if self._label_ids is not None else default
        return default


class HermesPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin = load_plugin()

    def test_hp01_plugin_yaml_metadata(self):
        text = YAML_PATH.read_text(encoding="utf-8")
        name = None
        version = None
        hooks = []
        in_hooks = False
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("version:"):
                version = line.split(":", 1)[1].strip()
            elif line.startswith("provides_hooks:"):
                in_hooks = True
            elif in_hooks and line.startswith("- "):
                hooks.append(line[2:].strip())
            elif in_hooks and line and not line.startswith("-"):
                in_hooks = False
        self.assertEqual(name, "aaf-gate")
        self.assertEqual(version, self.plugin.PLUGIN_VERSION)
        self.assertEqual(hooks, ["pre_tool_call", "post_tool_call"])

    def test_hp02_ast_imports_and_no_comments(self):
        text = PLUGIN_PATH.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped:
                self.assertFalse(stripped.startswith("#"))
        tree = ast.parse(text)
        allowed = frozenset({"hashlib", "json", "os", "re", "socket"})
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(alias.name.startswith("framework"))
                    self.assertIn(alias.name.split(".")[0], allowed)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                self.assertFalse(module.startswith("framework"))

    def test_hp03_hash_value_equivalence(self):
        values = [
            {"nested": {"a": 1, "b": ["x", "y"]}},
            {"text": "Grüße"},
            [1, 2, 3],
            {"n": 42, "f": 1.5},
            {},
            {"z": 1, "a": 2},
        ]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(self.plugin.hash_value(value), canon.hash_value(value))
        with self.assertRaises(ValueError):
            self.plugin.hash_value({"bad": float("nan")})
        with self.assertRaises(ValueError):
            canon.hash_value({"bad": float("nan")})

    def test_hp04_build_request_equivalence(self):
        self.assertEqual(self.plugin.REQUEST_SCHEMA, gate.REQUEST_SCHEMA)
        cases = [
            ("gmail", {"body": "hi"}, None),
            ("files", {"path": "/tmp/x"}, {"files": "path"}),
            ("gmail", {"to": "a@b.c", "body": "x"}, {"gmail": "to"}),
        ]
        for tool, args, target_keys in cases:
            with self.subTest(tool=tool):
                a = self.plugin.build_request(tool, args, target_keys)
                b = hermes_adapter.build_request(tool, args, target_keys)
                self.assertEqual(a, b)
                self.assertEqual(self.plugin.rule_key(a), hermes_adapter.rule_key(b))

    def test_hp05_callback_equivalence(self):
        checks = [
            lambda req: {"decision": "allow", "reason": "ok"},
            lambda req: {"decision": "deny", "reason": "action_class_unknown"},
            lambda req: {"decision": "deny", "reason": ""},
            lambda req: {"decision": "needs_approval", "action_class": "send"},
            lambda req: {"decision": "needs_approval", "action_class": None},
            lambda req: "allow",
            lambda req: (_ for _ in ()).throw(RuntimeError("x")),
        ]
        args = {"id": "1"}
        for check in checks:
            with self.subTest(check=check):
                p_cb = self.plugin.make_pre_tool_call(check)
                f_cb = hermes_adapter.make_pre_tool_call(check)
                self.assertEqual(p_cb("gmail", args), f_cb("gmail", args))
        p_cb = self.plugin.make_pre_tool_call(lambda req: {"decision": "allow"})
        f_cb = hermes_adapter.make_pre_tool_call(lambda req: {"decision": "allow"})
        self.assertEqual(p_cb("gmail", []), f_cb("gmail", []))

    def test_hp06_register_unconfigured(self):
        ctx = FakeCtx()
        with patch.dict(os.environ, {}, clear=True):
            bare = FakeCtx()
            self.plugin.register(bare)
            self.assertEqual(
                bare.hooks[0][1]("gmail", {"x": 1})["message"],
                "Kern: verweigert (gate_socket_unconfigured)",
            )
        with patch.dict(os.environ, {"AAF_GATE_SOCKET": "/tmp/ignored.sock"}):
            self.plugin.register(ctx)
        self.assertEqual(len(ctx.hooks), 1)
        self.assertEqual(ctx.hooks[0][0], "pre_tool_call")
        out = ctx.hooks[0][1]("gmail", {"x": 1})
        self.assertEqual(out["message"], "Kern: verweigert (gate_socket_unconfigured)")

    def test_hp07_e2e_socket(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        sock_path = folder + "/g.sock"
        captured = []

        def fake_check(req):
            captured.append(req)
            if req["tool"] == "gmail_send":
                return {
                    "decision": "needs_approval",
                    "action_class": "external_send",
                    "reason": "approval_required",
                }
            return {"decision": "allow", "reason": "ok"}

        server = GateServer(sock_path, fake_check)
        server.start()
        time.sleep(0.05)
        try:
            ctx = ConfigFakeCtx(socket_path=sock_path)
            self.plugin.register(ctx)
            pre = next(cb for name, cb in ctx.hooks if name == "pre_tool_call")
            cb = pre
            args_read = {"id": "READ-SECRET-4711"}
            args_send = {"to": "x@example.invalid", "body": "BODY-SECRET-88"}
            self.assertIsNone(cb("gmail_read", args_read))
            approve = cb("gmail_send", args_send)
            self.assertEqual(approve["action"], "approve")
            content_hash = self.plugin.hash_value(args_send)
            self.assertEqual(approve["rule_key"], "aaf:gmail_send:call:" + content_hash)
            for req in captured:
                blob = json.dumps(req, ensure_ascii=False)
                for value in list(args_read.values()) + list(args_send.values()):
                    self.assertNotIn(str(value), blob)
                self.assertIn("content_hash", req)
        finally:
            server.close()

    def test_hp08_fail_closed_unreachable(self):
        missing = os.path.abspath("/tmp/aaf-missing-" + str(os.getpid()) + ".sock")
        ctx = ConfigFakeCtx(socket_path=missing)
        self.plugin.register(ctx)
        pre = next(cb for name, cb in ctx.hooks if name == "pre_tool_call")
        out = pre("gmail", {"id": "1"})
        self.assertEqual(out["message"], "Kern: verweigert (core_unreachable)")

    def test_hp09_post_equivalence_with_framework(self):
        plugin_reports = []
        frame_reports = []
        args = {"a": "1"}

        def run_pair(pre_args, post_args, tool_call_id):
            pledger = self.plugin.DecisionLedger()
            fledger = hermes_adapter.DecisionLedger()
            ppost = self.plugin.make_post_tool_call(plugin_reports.append, pledger)
            fpost = hermes_adapter.make_post_tool_call(frame_reports.append, fledger)
            pre = self.plugin.make_pre_tool_call(lambda r: {"decision": "allow"}, ledger=pledger)
            fpre = hermes_adapter.make_pre_tool_call(lambda r: {"decision": "allow"}, ledger=fledger)
            pre("tool", pre_args, tool_call_id=tool_call_id)
            fpre("tool", pre_args, tool_call_id=tool_call_id)
            p_out = ppost("tool", post_args, tool_call_id=tool_call_id)
            f_out = fpost("tool", post_args, tool_call_id=tool_call_id)
            return p_out, f_out

        p, f = run_pair(args, args, "c1")
        self.assertIsNone(p)
        self.assertIsNone(f)
        self.assertEqual(plugin_reports, frame_reports)
        plugin_reports.clear()
        frame_reports.clear()
        run_pair(args, {"a": "2"}, "c2")
        self.assertEqual(plugin_reports, frame_reports)
        self.assertEqual(len(plugin_reports), 1)
        self.assertEqual(plugin_reports[0]["kind"], "gate_mismatch")
        plugin_reports.clear()
        frame_reports.clear()
        post = self.plugin.make_post_tool_call(plugin_reports.append, self.plugin.DecisionLedger())
        fpost = hermes_adapter.make_post_tool_call(frame_reports.append, hermes_adapter.DecisionLedger())
        post("t", {"x": "1"}, tool_call_id="unknown")
        fpost("t", {"x": "1"}, tool_call_id="unknown")
        self.assertEqual(plugin_reports[0]["kind"], "gate_unseen")
        self.assertEqual(plugin_reports, frame_reports)
        noisy_p = self.plugin.make_post_tool_call(
            lambda o: (_ for _ in ()).throw(RuntimeError("x")), self.plugin.DecisionLedger()
        )
        self.assertIsNone(noisy_p("t", {"a": 1}, tool_call_id="z"))
        self.assertIsNone(
            self.plugin.make_post_tool_call(plugin_reports.append, self.plugin.DecisionLedger())(
                "t", {"x": float("nan")}
            )
        )
        ledger = self.plugin.DecisionLedger()
        for i in range(self.plugin.LEDGER_MAX + 1):
            ledger.record(f"id-{i}", f"sha256:{'0' * 64}")
        self.assertEqual(len(ledger), self.plugin.LEDGER_MAX)
        obs_p = self.plugin.build_observation("g", "gate_unseen", None, "sha256:" + "2" * 64)
        obs_f = hermes_adapter.build_observation("g", "gate_unseen", None, "sha256:" + "2" * 64)
        self.assertEqual(obs_p, obs_f)

    def test_hp10_register_pre_post_no_observation_on_match(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        sock_path = folder + "/g.sock"
        kinds = []

        def check(req):
            kinds.append("request")
            return {"decision": "allow", "reason": "ok"}

        def observe(obs):
            kinds.append("observation")
            return {"ok": True, "reason": None, "audit_sequence": 1}

        server = GateServer(sock_path, check, observe)
        server.start()
        time.sleep(0.05)
        try:
            ctx = ConfigFakeCtx(socket_path=sock_path)
            self.plugin.register(ctx)
            names = [name for name, _ in ctx.hooks]
            self.assertEqual(names, ["pre_tool_call", "post_tool_call"])
            pre = next(cb for name, cb in ctx.hooks if name == "pre_tool_call")
            post = next(cb for name, cb in ctx.hooks if name == "post_tool_call")
            args = {"n": 1}
            pre("gmail_read", args, tool_call_id="c1")
            post("gmail_read", args, tool_call_id="c1")
            self.assertEqual(kinds, ["request"])
        finally:
            server.close()

    def test_hp11_config_socket_overrides_env(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        sock_path = folder + "/g.sock"
        captured = []

        def fake_check(req):
            captured.append(req)
            if req["tool"] == "gmail_send":
                return {
                    "decision": "needs_approval",
                    "action_class": "external_send",
                    "reason": "approval_required",
                }
            return {"decision": "allow", "reason": "ok"}

        server = GateServer(sock_path, fake_check)
        server.start()
        time.sleep(0.05)
        bad = os.path.abspath("/tmp/aaf-missing-hp11-" + str(os.getpid()) + ".sock")
        try:
            ctx = ConfigFakeCtx(socket_path=sock_path)
            with patch.dict(os.environ, {"AAF_GATE_SOCKET": bad}):
                self.plugin.register(ctx)
            pre = next(cb for name, cb in ctx.hooks if name == "pre_tool_call")
            args_read = {"id": "READ-SECRET-4711"}
            args_send = {"to": "x@example.invalid", "body": "BODY-SECRET-88"}
            self.assertIsNone(pre("gmail_read", args_read))
            approve = pre("gmail_send", args_send)
            self.assertEqual(approve["action"], "approve")
            content_hash = self.plugin.hash_value(args_send)
            self.assertEqual(approve["rule_key"], "aaf:gmail_send:call:" + content_hash)
            for req in captured:
                blob = json.dumps(req, ensure_ascii=False)
                for value in list(args_read.values()) + list(args_send.values()):
                    self.assertNotIn(str(value), blob)
                self.assertIn("content_hash", req)
        finally:
            server.close()

    def test_hp12_config_relative_blocks_despite_valid_env(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        sock_path = folder + "/g.sock"
        server = GateServer(sock_path, lambda req: {"decision": "allow", "reason": "ok"})
        server.start()
        time.sleep(0.05)
        try:
            ctx = ConfigFakeCtx(socket_path="relativ/aaf.sock")
            with patch.dict(os.environ, {"AAF_GATE_SOCKET": sock_path}):
                self.plugin.register(ctx)
            self.assertEqual(len(ctx.hooks), 1)
            out = ctx.hooks[0][1]("gmail", {"x": 1})
            self.assertEqual(out["message"], "Kern: verweigert (gate_socket_unconfigured)")
        finally:
            server.close()

    def test_hp13_config_invalid_or_raises_blocks(self):
        ctx_int = ConfigFakeCtx(socket_path=5)
        with patch.dict(os.environ, {"AAF_GATE_SOCKET": "/tmp/nope.sock"}):
            self.plugin.register(ctx_int)
        out_int = ctx_int.hooks[0][1]("gmail", {"x": 1})
        self.assertEqual(out_int["message"], "Kern: verweigert (gate_socket_unconfigured)")
        ctx_err = ConfigFakeCtx(get_config_raises=RuntimeError("boom"))
        with patch.dict(os.environ, {"AAF_GATE_SOCKET": "/tmp/nope.sock"}):
            self.plugin.register(ctx_err)
        out_err = ctx_err.hooks[0][1]("gmail", {"x": 1})
        self.assertEqual(out_err["message"], "Kern: verweigert (gate_socket_unconfigured)")

    def test_hp14_config_none_and_no_get_config_block_env(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        sock_path = folder + "/g.sock"
        server = GateServer(sock_path, lambda req: {"decision": "allow", "reason": "ok"})
        server.start()
        time.sleep(0.05)
        try:
            ctx = ConfigFakeCtx(socket_path=None)
            with patch.dict(os.environ, {"AAF_GATE_SOCKET": sock_path}):
                self.plugin.register(ctx)
            pre = next(cb for name, cb in ctx.hooks if name == "pre_tool_call")
            out = pre("gmail_read", {"id": "1"})
            self.assertEqual(out["message"], "Kern: verweigert (gate_socket_unconfigured)")
            bare = FakeCtx()
            with patch.dict(os.environ, {"AAF_GATE_SOCKET": sock_path}):
                self.plugin.register(bare)
            pre_bare = next(cb for name, cb in bare.hooks if name == "pre_tool_call")
            self.assertEqual(
                pre_bare("gmail_read", {"id": "2"})["message"],
                "Kern: verweigert (gate_socket_unconfigured)",
            )
        finally:
            server.close()

    def test_hp16_env_without_socket_path_blocks(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        sock_path = folder + "/g.sock"
        server = GateServer(sock_path, lambda req: {"decision": "allow", "reason": "ok"})
        server.start()
        time.sleep(0.05)
        try:
            ctx = FakeCtx()
            with patch.dict(os.environ, {"AAF_GATE_SOCKET": sock_path}):
                self.plugin.register(ctx)
            out = ctx.hooks[0][1]("gmail", {"x": 1})
            self.assertEqual(out["message"], "Kern: verweigert (gate_socket_unconfigured)")
        finally:
            server.close()

    def test_hp17_config_socket_ignores_env(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        sock_path = folder + "/g.sock"
        captured = []
        server = GateServer(sock_path, lambda req: captured.append(req) or {"decision": "allow", "reason": "ok"})
        server.start()
        time.sleep(0.05)
        bad = os.path.abspath("/tmp/aaf-missing-hp17-" + str(os.getpid()) + ".sock")
        try:
            ctx = ConfigFakeCtx(socket_path=sock_path)
            with patch.dict(os.environ, {"AAF_GATE_SOCKET": bad}):
                self.plugin.register(ctx)
            pre = next(cb for name, cb in ctx.hooks if name == "pre_tool_call")
            self.assertIsNone(pre("gmail_read", {"id": "1"}))
            self.assertEqual(len(captured), 1)
        finally:
            server.close()

    def test_hp18_empty_socket_path_blocks(self):
        for value in ("", "   "):
            with self.subTest(socket_path=value):
                ctx = ConfigFakeCtx(socket_path=value)
                with patch.dict(os.environ, {"AAF_GATE_SOCKET": "/tmp/ignored.sock"}):
                    self.plugin.register(ctx)
                out = ctx.hooks[0][1]("gmail", {"x": 1})
                self.assertEqual(out["message"], "Kern: verweigert (gate_socket_unconfigured)")

    def test_hp15_no_config_schema_or_set_config(self):
        text = YAML_PATH.read_text(encoding="utf-8")
        self.assertNotIn("config_schema", text)
        plugin_text = PLUGIN_PATH.read_text(encoding="utf-8")
        self.assertNotIn("set_config", plugin_text)


if __name__ == "__main__":
    unittest.main()