import unittest

from framework.gate import decide

ZERO_HASH = "sha256:" + "0" * 64


def sample_request(**overrides):
    payload = {
        "schema_version": "gate-request-0.1-draft",
        "runtime": "local",
        "tool": "gmail",
        "action": "read",
        "target": "inbox",
        "content_hash": ZERO_HASH,
    }
    payload.update(overrides)
    return payload


def sample_policy(**overrides):
    payload = {
        "schema_version": "gate-policy-0.1-draft",
        "tool_classes": {
            "gmail:read": "read",
            "gmail:draft": "draft",
            "gmail:send": "send",
            "gmail:*": "delete_external",
            "files:write": "internal_write",
        },
        "levels": {
            "read": "L4",
            "draft": "L4",
            "send": "L3",
            "internal_write": "L4",
        },
    }
    payload.update(overrides)
    return payload


class GateTests(unittest.TestCase):
    def test_ga01_gmail_read_allow(self):
        out = decide(sample_request(), sample_policy())
        self.assertEqual(out["decision"], "allow")
        self.assertEqual(out["reason"], "ok")
        self.assertEqual(out["effective_level"], "L4")
        self.assertEqual(out["action_class"], "read")
        self.assertTrue(out["request_hash"].startswith("sha256:"))

    def test_ga02_gmail_send_needs_approval(self):
        out = decide(sample_request(action="send"), sample_policy())
        self.assertEqual(out["decision"], "needs_approval")
        self.assertEqual(out["reason"], "approval_required")
        self.assertEqual(out["effective_level"], "L3")

    def test_ga03_gmail_delete_level_l0(self):
        out = decide(sample_request(action="delete"), sample_policy())
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "level_L0")
        self.assertEqual(out["action_class"], "delete_external")

    def test_ga04_unknown_tool(self):
        out = decide(sample_request(tool="terminal", action="run"), sample_policy())
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "action_class_unknown")
        self.assertIsNone(out["action_class"])

    def test_ga05_extra_request_key(self):
        req = sample_request()
        req["extra"] = "x"
        out = decide(req, sample_policy())
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "invalid_request")
        self.assertIsNone(out["request_hash"])

    def test_ga06_content_hash_invalid(self):
        policy = sample_policy()
        for bad in ("0" * 64, "sha256:" + "A" * 64):
            with self.subTest(content_hash=bad):
                out = decide(sample_request(content_hash=bad), policy)
                self.assertEqual(out["decision"], "deny")
                self.assertEqual(out["reason"], "invalid_request")

    def test_ga07_control_char_in_target(self):
        out = decide(sample_request(target="bad\n"), sample_policy())
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "invalid_request")

    def test_ga08_policy_level_above_core_max(self):
        bad = sample_policy(levels={**sample_policy()["levels"], "send": "L4"})
        out = decide(sample_request(action="send"), bad)
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "invalid_policy")
        self.assertTrue(out["request_hash"].startswith("sha256:"))

    def test_ga09_policy_unknown_tool_class_value(self):
        classes = dict(sample_policy()["tool_classes"])
        classes["other:act"] = "unknown_kind"
        out = decide(sample_request(), sample_policy(tool_classes=classes))
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "invalid_policy")

    def test_ga10_unattended_cap(self):
        policy = sample_policy()
        write = decide(
            sample_request(tool="files", action="write"),
            policy,
            unattended=True,
        )
        self.assertEqual(write["decision"], "deny")
        self.assertEqual(write["reason"], "level_L2")
        read = decide(sample_request(), policy, unattended=True)
        self.assertEqual(read["decision"], "allow")

    def test_ga11_determinism(self):
        req = sample_request()
        req2 = sample_request(content_hash="sha256:" + "1" * 64)
        policy = sample_policy()
        first = decide(req, policy)
        second = decide(req, policy)
        self.assertEqual(first, second)
        third = decide(req2, policy)
        self.assertNotEqual(first["request_hash"], third["request_hash"])
        self.assertEqual(first["policy_hash"], third["policy_hash"])

    def test_ga12_never_raises(self):
        policy = sample_policy()
        cases = (
            (None, policy, False),
            ([], policy, False),
            ({"schema_version": "gate-request-0.1-draft", "runtime": 1, "tool": "gmail", "action": "read", "target": "inbox", "content_hash": ZERO_HASH}, policy, False),
            (sample_request(), policy, "yes"),
        )
        for request, pol, unattended in cases:
            with self.subTest(request=request, unattended=unattended):
                out = decide(request, pol, unattended=unattended)
                self.assertEqual(out["decision"], "deny")


if __name__ == "__main__":
    unittest.main()