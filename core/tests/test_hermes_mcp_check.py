import unittest

from framework import hermes_mcp


class HermesMcpCheckTests(unittest.TestCase):
    def test_mc01_server_entry_is_clean(self):
        self.assertEqual(hermes_mcp.check_mcp_servers(hermes_mcp.hermes_server_entry()), [])
        self.assertEqual(
            hermes_mcp.check_mcp_servers(
                hermes_mcp.hermes_server_entry("gmailwork"),
                allowed=("gmailwork",),
            ),
            [],
        )

    def test_mc02_not_dict(self):
        want = [{"code": "mcp_servers_invalid", "server": None}]
        for value in (None, [], "gmail", 5):
            with self.subTest(value=value):
                self.assertEqual(hermes_mcp.check_mcp_servers(value), want)

    def test_mc03_names(self):
        e = hermes_mcp.hermes_server_entry()["gmail"]
        data = {"Gmail": e, "gmail-ro": e, 7: e, "github": e}
        out = hermes_mcp.check_mcp_servers(data)
        codes = [row["code"] for row in out]
        self.assertEqual(codes.count("server_name_invalid"), 1)
        servers_invalid = [row for row in out if row["code"] == "server_name_invalid"]
        self.assertEqual(servers_invalid, [{"code": "server_name_invalid", "server": None}])
        self.assertIn({"code": "server_not_allowed", "server": "github"}, out)
        for row in out:
            if row["code"] in ("server_name_invalid", "server_not_allowed"):
                continue
            self.fail(row)
        for bad in ("Gmail", "gmail-ro"):
            self.assertFalse(any(row.get("server") == bad for row in out))

    def test_mc04_prefix_collision(self):
        e = hermes_mcp.hermes_server_entry()["gmail"]
        collision = hermes_mcp.check_mcp_servers({"gmail": e, "gmail-work": e})
        self.assertIn({"code": "prefix_collision", "server": None}, collision)
        ok = hermes_mcp.check_mcp_servers(
            {"gmail": e, "gmailwork": e},
            allowed=("gmail", "gmailwork"),
        )
        self.assertNotIn({"code": "prefix_collision", "server": None}, ok)

    def test_mc05_include_rules(self):
        base = dict(hermes_mcp.hermes_server_entry()["gmail"])
        no_tools = dict(base)
        del no_tools["tools"]
        self.assertIn(
            {"code": "include_missing", "server": "gmail"},
            hermes_mcp.check_mcp_servers({"gmail": no_tools}),
        )
        bad_send = dict(base)
        bad_send["tools"] = {"include": ["get_message", "send_message"]}
        self.assertIn(
            {"code": "include_not_allowed", "server": "gmail"},
            hermes_mcp.check_mcp_servers({"gmail": bad_send}),
        )
        bad_type = dict(base)
        bad_type["tools"] = {"include": ["get_message", 3]}
        self.assertIn(
            {"code": "include_missing", "server": "gmail"},
            hermes_mcp.check_mcp_servers({"gmail": bad_type}),
        )
        with_exclude = dict(base)
        with_exclude["tools"] = dict(base["tools"])
        with_exclude["tools"]["exclude"] = []
        self.assertIn(
            {"code": "exclude_present", "server": "gmail"},
            hermes_mcp.check_mcp_servers({"gmail": with_exclude}),
        )

    def test_mc06_url_and_keys(self):
        e = hermes_mcp.hermes_server_entry()["gmail"]
        wrong_url = dict(e)
        wrong_url["url"] = "https://example.invalid/mcp"
        self.assertIn(
            {"code": "url_mismatch", "server": "gmail"},
            hermes_mcp.check_mcp_servers({"gmail": wrong_url}),
        )
        headers = dict(e)
        headers["headers"] = {}
        self.assertIn(
            {"code": "entry_key_unknown", "server": "gmail"},
            hermes_mcp.check_mcp_servers({"gmail": headers}),
        )
        bad_enabled = dict(e)
        bad_enabled["enabled"] = "yes"
        self.assertIn(
            {"code": "entry_invalid", "server": "gmail"},
            hermes_mcp.check_mcp_servers({"gmail": bad_enabled}),
        )
        disabled = dict(e)
        disabled["enabled"] = False
        self.assertEqual(hermes_mcp.check_mcp_servers({"gmail": disabled}), [])
        self.assertEqual(
            hermes_mcp.check_mcp_servers({"gmail": "x"}),
            [{"code": "entry_invalid", "server": "gmail"}],
        )

    def test_mc07_sorted_and_never_raises(self):
        data = {"github": 1, "gmail": {"url": 1, "tools": {"include": None, "foo": 1}}}
        out = hermes_mcp.check_mcp_servers(data)
        ordered = sorted(out, key=lambda row: (row["code"], row["server"] or ""))
        self.assertEqual(out, ordered)
        keys = [(row["code"], row["server"] or "") for row in out]
        self.assertEqual(len(keys), len(set(keys)))
        hermes_mcp.check_mcp_servers({"gmail": {"tools": object()}})
        hermes_mcp.check_mcp_servers(
            {
                "gmail": {
                    "url": hermes_mcp.GMAIL_MCP_URL,
                    "tools": {"include": ("get_message",)},
                }
            }
        )


if __name__ == "__main__":
    unittest.main()