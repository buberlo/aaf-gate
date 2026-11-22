import ast
import re
import unittest
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parents[1] / "framework"

WRITE_PATTERN = re.compile(r"\b(INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)")
TABLE_LITERAL_PATTERN = re.compile(r"\b(INSERT INTO|UPDATE|DELETE FROM)\s+[a-z_]+\b")
CREATE_TABLE_PATTERN = re.compile(r"CREATE TABLE(?: IF NOT EXISTS)?\s+([a-z_]+)")

NO_DB_WRITE_MODULES = frozenset(
    {
        "appliance",
        "cli",
        "canon",
        "clock",
        "egress",
        "errors",
        "gate",
        "gatesocket",
        "hermes_adapter",
        "hermes_mcp",
        "idempotency",
        "policy",
        "principals",
        "releases",
        "rules",
        "templates",
        "version",
        "devicemode",
        "hookcheck",
        "backup",
        "hermesprobe",
        "installcheck",
    }
)

EXPECTED = (
    ("approvals", "service", "_op_approve"),
    ("approvals", "service", "_op_revise"),
    ("audit_events", "audit", "append"),
    ("audit_heads", "audit", "append"),
    ("claims", "service", "_replace_claims"),
    ("config_active", "configplane", "_release_new"),
    ("config_active", "configplane", "_rollback"),
    ("config_idempotency", "configplane", "wrapped"),
    ("config_releases", "configplane", "_release_new"),
    ("config_templates", "configplane", "work"),
    ("feedback", "service", "_op_save_feedback"),
    ("receipts", "service", "_complete"),
    ("rule_sets", "service", "_op_activate_rules"),
    ("rule_sets", "service", "_op_withdraw_rules"),
    ("runs", "service", "_attach_source"),
    ("runs", "service", "_cas"),
    ("runs", "service", "_insert_run"),
    ("source_revisions", "service", "_op_register_source"),
    ("principals", "store", "add_principal"),
    ("principals", "store", "disable_principal"),
    ("role_bindings", "store", "bind_role"),
    ("role_bindings", "store", "unbind_role"),
    ("gate_stops", "stopswitch", "_set_state"),
    ("gate_calls", "gatekeeper", "check"),
)


class WriteSiteVisitor(ast.NodeVisitor):
    def __init__(self, module_name: str):
        self.module_name = module_name
        self.function_stack: list[str] = []
        self.sites: set[tuple[str, str, str]] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self._collect(node.value)
        self.generic_visit(node)

    def _collect(self, text: str) -> None:
        func = self.function_stack[-1] if self.function_stack else "<module>"
        for match in WRITE_PATTERN.finditer(text):
            self.sites.add((match.group(2), self.module_name, func))


class JoinedStrVisitor(ast.NodeVisitor):
    def __init__(self):
        self.violations: list[str] = []

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        parts = [
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        ]
        text = "".join(parts)
        if "INSERT" in text or "UPDATE" in text or "DELETE" in text:
            if TABLE_LITERAL_PATTERN.search(text) is None:
                self.violations.append(text)
        self.generic_visit(node)


def scan_write_sites() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path in sorted(FRAMEWORK.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        visitor = WriteSiteVisitor(path.stem)
        visitor.visit(tree)
        found |= visitor.sites
    return found


def scan_joinedstr_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(FRAMEWORK.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        visitor = JoinedStrVisitor()
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


def schema_tables() -> set[str]:
    text = (FRAMEWORK / "store.py").read_text(encoding="utf-8")
    return set(CREATE_TABLE_PATTERN.findall(text))


class ArchitectureTests(unittest.TestCase):
    def test_write_sites_are_frozen(self):
        found = scan_write_sites()
        expected = set(EXPECTED)
        missing = expected - found
        extra = found - expected
        if missing or extra:
            parts = []
            if missing:
                parts.append("missing " + ", ".join(str(item) for item in sorted(missing)))
            if extra:
                parts.append("extra " + ", ".join(str(item) for item in sorted(extra)))
            self.fail("; ".join(parts))
        self.assertEqual(found, expected)

    def test_modules_without_db_writes(self):
        found = scan_write_sites()
        for module in NO_DB_WRITE_MODULES:
            hits = {item for item in found if item[1] == module}
            self.assertEqual(hits, set(), msg=module)

    def test_table_names_are_literal(self):
        self.assertEqual(scan_joinedstr_violations(), [])

    def test_every_table_has_a_writer(self):
        writers = {table for table, _module, _func in EXPECTED}
        tables = schema_tables()
        missing = tables - writers
        self.assertEqual(missing, set(), msg="tables without writer " + ", ".join(sorted(missing)))

    def test_audit_events_single_writer_module(self):
        modules = {module for table, module, _func in EXPECTED if table == "audit_events"}
        self.assertEqual(modules, {"audit"})


if __name__ == "__main__":
    unittest.main()
