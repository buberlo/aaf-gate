import unittest

from framework import hermes_mcp


class HermesMcpReservedTests(unittest.TestCase):
    def test_mr01_constants(self):
        self.assertEqual(hermes_mcp.HERMES_PINNED_VERSION, "0.23.1")
        self.assertEqual(len(hermes_mcp.RESERVED_SERVER_NAMES), 23)
        for name in hermes_mcp.RESERVED_SERVER_NAMES:
            self.assertIsNotNone(hermes_mcp.SERVER_NAME.fullmatch(name))
        self.assertNotIn("gmail", hermes_mcp.RESERVED_SERVER_NAMES)

    def test_mr02_reserved_rejected_even_if_allowed(self):
        entry = hermes_mcp.hermes_server_entry()["gmail"]
        out = hermes_mcp.check_mcp_servers({"memory": entry}, allowed=("memory",))
        self.assertEqual(out, [{"code": "server_name_reserved", "server": "memory"}])

    def test_mr03_reserved_not_allowed_only_reserved(self):
        entry = hermes_mcp.hermes_server_entry()["gmail"]
        out = hermes_mcp.check_mcp_servers({"gmail": entry, "browser": {"x": 1}})
        self.assertEqual(out, [{"code": "server_name_reserved", "server": "browser"}])

    def test_mr04_builders_raise(self):
        cases = [
            lambda: hermes_mcp.registered_name("todo", "get_message"),
            lambda: hermes_mcp.pilot1_policy("web"),
            lambda: hermes_mcp.hermes_server_entry("file"),
        ]
        for call in cases:
            with self.subTest(call=call):
                with self.assertRaises(ValueError) as ctx:
                    call()
                self.assertEqual(str(ctx.exception), "reserved_mcp_name")
        with self.assertRaises(ValueError) as ctx:
            hermes_mcp.registered_name("Todo", "x")
        self.assertEqual(str(ctx.exception), "invalid_mcp_name")

    def test_mr05_existing_behavior_kept(self):
        self.assertEqual(hermes_mcp.check_mcp_servers(hermes_mcp.hermes_server_entry()), [])
        default = hermes_mcp.pilot1_policy()
        gmail = hermes_mcp.pilot1_policy("gmail")
        self.assertEqual(default, gmail)
        self.assertEqual(len(default["tool_classes"]), 10)


if __name__ == "__main__":
    unittest.main()