import hashlib
import json
import plistlib
import re
from pathlib import Path

from framework.canon import hash_value, render_value
from framework.configplane import ConfigError
from framework import hermes_mcp
from framework.launchd import gate_plist
from framework.launchdcheck import install_map_body
from framework.pinaudit import audit_plugins, disabled_from_config
from framework.plugincheck import plugin_digest
from framework.devicecheck import PROFILE_ID_RE
from framework.hermes_adapter import label_allowlist
from framework.hermesprobe import PROBE_SCRIPT
from framework.version import VERSION
from framework.templates import template_key

PLUGINS_ENABLED = ("aaf-gate",)
PLUGINS_DISABLED = ("disk-cleanup", "raft-platform", "security-guidance")

ACCEPTANCE_STEP_ORDER = (
    "verify-hermes-dump",
    "G1",
    "G2",
    "G3",
    "S1",
    "S2",
    "S3",
    "S4",
    "S5",
    "S6",
    "S7",
    "S8",
    "S9",
    "gate-audit-anchor",
    "gate-backup",
    "gate-restore",
    "verify-device",
)

ACCEPTANCE_STEP_SUFFIX = {
    "G2": "failed on plugins_enabled_mismatch or label_ids_mismatch",
    "G3": "passed only when gate_ignored is 0",
    "S9": "summary.stops with stop and resume at least 1, rejected at least 1, stopped false",
    "gate-audit-anchor": (
        "Record anchor value; on next acceptance pass to verify-device --audit-anchor"
    ),
    "gate-backup": (
        "Record anchor per tenant from the manifest; keep backup and manifest off device"
    ),
    "gate-restore": (
        "Probe into a new path with --expect-anchor; passed only when ok true and stopped matches gate-status before backup"
    ),
    "verify-device": "Line with package_hash, release_hash and hermes_commit",
}

GATE_SERVICE_KEYS = frozenset(
    {
        "label",
        "python",
        "workdir",
        "db",
        "socket_path",
        "principal",
        "user",
        "group",
        "server",
        "mode",
        "unattended",
        "log_path",
    }
)
GATE_SERVICE_REQUIRED = frozenset(
    {"label", "python", "workdir", "db", "socket_path", "principal", "user"}
)

DOCUMENTED_MANAGED_KEYS = (
    "approvals.mode",
    "approvals.cron_mode",
    "approvals.unattended_mode",
    "computer_use.permission_mode",
)


def build_appliance_package(
    plane,
    principal_id: str,
    config_id: str,
    destination: Path,
    expected: dict | None = None,
    plugin_dir: Path | None = None,
    gate_service: dict | None = None,
    hermes_source: Path | None = None,
    hermes_commit: str | None = None,
    customer_profile: str | None = None,
    label_ids: list | None = None,
) -> dict:
    if customer_profile is not None:
        if customer_profile == "default" or PROFILE_ID_RE.fullmatch(customer_profile) is None:
            raise ConfigError("CUSTOMER_PROFILE_INVALID")
    if label_ids is not None:
        if gate_service is None:
            raise ConfigError("LABEL_IDS_WITHOUT_GATE")
        allowed = label_allowlist(label_ids)
        if allowed is None or len(allowed) != len(label_ids):
            raise ConfigError("LABEL_IDS_INVALID")
        label_ids = sorted(label_ids)
    plane.require(principal_id, "author")
    current = plane.release_record(principal_id, config_id)
    previous = None
    if current["release_no"] > 1:
        previous = plane.release_record(principal_id, config_id, current["release_no"] - 1)
    if expected is not None:
        document = current["document"]
        key = template_key(document["template_id"], document["template_version"])
        if key not in expected:
            raise ConfigError("TEMPLATE_UNLISTED")
        if document["template_body_hash"] != expected[key]:
            raise ConfigError("TEMPLATE_HASH_MISMATCH")
    hermes_plugin_digest = None
    if plugin_dir is not None:
        hermes_plugin_digest = plugin_digest(plugin_dir)
    gate_launchd_label = None
    gate_plist_sha256 = None
    launchd_plist = None
    if gate_service is not None:
        gate_launchd_label, gate_plist_sha256, launchd_plist = _prepare_gate_service(gate_service)
    pin_audit_hash = None
    if hermes_commit is not None and hermes_source is None:
        raise ConfigError("HERMES_SOURCE_MISSING")
    if hermes_commit is not None and re.fullmatch(r"[0-9a-f]{40}", hermes_commit) is None:
        raise ConfigError("HERMES_COMMIT_INVALID")
    if hermes_source is not None:
        result = audit_plugins(hermes_source, set(disabled_from_config(_managed_config())))
        if result["issues"]:
            raise ConfigError("PIN_AUDIT_FAILED")
        pin_audit_hash = hash_value(result)
    destination.mkdir(parents=True, exist_ok=False)
    files = _files(
        current,
        previous,
        expected,
        hermes_plugin_digest,
        gate_launchd_label,
        gate_plist_sha256,
        launchd_plist,
        gate_service=gate_service,
        hermes_commit=hermes_commit,
        pin_audit_hash=pin_audit_hash,
        customer_profile=customer_profile,
        label_ids=label_ids,
    )
    for relative, text in files.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return {
        "package_hash": hash_value(files),
        "release_hash": current["release_hash"],
        "release_no": current["release_no"],
    }


def _prepare_gate_service(gate_service):
    if not isinstance(gate_service, dict):
        raise ConfigError("GATE_SERVICE_INVALID")
    keys = set(gate_service)
    if keys - GATE_SERVICE_KEYS or GATE_SERVICE_REQUIRED - keys:
        raise ConfigError("GATE_SERVICE_INVALID")
    try:
        plist_bytes = gate_plist(**gate_service)
    except (TypeError, ValueError):
        raise ConfigError("GATE_SERVICE_INVALID") from None
    label = gate_service["label"]
    digest = hashlib.sha256(plist_bytes).hexdigest()
    return label, digest, plist_bytes.decode("utf-8")


def dry_run_lines(package_dir: Path) -> list[str]:
    provenance = json.loads((package_dir / "provenance.json").read_text(encoding="utf-8"))
    return [
        "DRY-RUN",
        "no system changes",
        "kein sudo",
        "kein Schreiben nach /etc",
        f"release {provenance['release_no']}",
        f"release_hash {provenance['release_hash']}",
        f"device_mode {provenance['device_mode']}",
        f"tenant_id {provenance.get('tenant_id')}",
        f"config_id {provenance.get('config_id')}",
        f"customer_profile {provenance.get('customer_profile') or '-'}",
        "label_ids "
        + (",".join(provenance.get("label_ids") or []) or "-"),
        "managed config would be a file in the documented format",
        "macOS path is not documented as a native managed-scope location",
    ]


def _files(
    current: dict,
    previous: dict | None,
    expected: dict | None = None,
    hermes_plugin_digest: str | None = None,
    gate_launchd_label: str | None = None,
    gate_plist_sha256: str | None = None,
    launchd_plist: str | None = None,
    gate_service: dict | None = None,
    hermes_commit: str | None = None,
    pin_audit_hash: str | None = None,
    customer_profile: str | None = None,
    label_ids: list | None = None,
) -> dict[str, str]:
    document = current["document"]
    soul = _soul(document)
    provenance_label_ids = None
    managed_label_ids = None
    if gate_service is not None:
        managed_label_ids = [] if label_ids is None else list(label_ids)
        provenance_label_ids = managed_label_ids
        managed = _managed_config(gate_service["socket_path"], managed_label_ids)
    else:
        managed = _managed_config()
    distribution = _distribution(document)
    ignore = "auth.json\n.env\n.env.EXAMPLE\nstate.db\nmemories/\nsessions/\nlogs/\n"
    assumptions = _assumptions()
    checklist = _checklist(
        gate_service,
        customer_profile,
        document["tenant_id"],
        managed_label_ids,
    )
    rollback = {
        "active_release_hash": current["release_hash"],
        "active_release_no": current["release_no"],
        "previous_release_hash": None if previous is None else previous["release_hash"],
        "previous_release_no": None if previous is None else previous["release_no"],
    }
    if expected is not None:
        device_mode = "appliance"
        hashes_file_hash = hash_value(expected)
    else:
        device_mode = "unlisted"
        hashes_file_hash = None
    provenance = {
        "config_id": document["config_id"],
        "core_floor_hash": document["core_floor_hash"],
        "core_schema_version": document["core_schema_version"],
        "customer_profile": customer_profile,
        "label_ids": provenance_label_ids,
        "device_mode": device_mode,
        "documented_managed_keys": list(DOCUMENTED_MANAGED_KEYS),
        "gate_launchd_label": gate_launchd_label,
        "gate_plist_sha256": gate_plist_sha256,
        "hashes_file_hash": hashes_file_hash,
        "hermes_commit": hermes_commit,
        "hermes_plugin_digest": hermes_plugin_digest,
        "pin_audit_hash": pin_audit_hash,
        "release_hash": current["release_hash"],
        "release_no": current["release_no"],
        "synthetic": True,
        "template_body_hash": document["template_body_hash"],
        "template_id": document["template_id"],
        "template_version": document["template_version"],
        "tenant_id": document["tenant_id"],
    }
    script = (
        "import json\n"
        "from pathlib import Path\n"
        "root = Path(__file__).resolve().parent\n"
        "provenance = json.loads((root / 'provenance.json').read_text(encoding='utf-8'))\n"
        "print('DRY-RUN')\n"
        "print('no system changes')\n"
        "print('kein sudo')\n"
        "print('kein Schreiben nach /etc')\n"
        "print('release ' + str(provenance['release_no']))\n"
        "print('release_hash ' + provenance['release_hash'])\n"
        "print('device_mode ' + provenance['device_mode'])\n"
        "print('tenant_id ' + str(provenance.get('tenant_id')))\n"
        "print('config_id ' + str(provenance.get('config_id')))\n"
        "print('customer_profile ' + str(provenance.get('customer_profile') or '-'))\n"
        "print('label_ids ' + (','.join(provenance.get('label_ids') or []) or '-'))\n"
    )
    files = {
        "ASSUMPTIONS.md": assumptions,
        "MANUAL-MAC-CHECKS.md": checklist,
        "distribution/.gitignore": ignore,
        "distribution/SOUL.md": soul,
        "distribution/config.yaml": _distribution_config(),
        "distribution/distribution.yaml": distribution,
        "managed/config.yaml": managed,
        "provenance.json": json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "provision_dry_run.py": script,
        "rollback.json": json.dumps(rollback, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    }
    if expected is not None:
        mode_body = {"hashes_path": "HASHES.json", "mode": "appliance"}
        files["device/device-mode.json"] = json.dumps(mode_body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        files["device/HASHES.json"] = json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if launchd_plist is not None and gate_launchd_label is not None:
        files["launchd/" + gate_launchd_label + ".plist"] = launchd_plist
        files["install-map.json"] = (
            json.dumps(install_map_body(gate_launchd_label), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
    if gate_service is not None:
        files["probes/hermes_probe.py"] = PROBE_SCRIPT
        files["ACCEPTANCE-RECORD.md"] = _acceptance_record(provenance)
    return files


def _acceptance_step_line(step_id: str) -> str:
    line = "- " + step_id + ": Output line: ____ | passed: yes/no"
    suffix = ACCEPTANCE_STEP_SUFFIX.get(step_id)
    if suffix:
        line += " | " + suffix
    return line


def _acceptance_record(provenance: dict) -> str:
    profile = provenance.get("customer_profile") or "-"
    label_ids = provenance.get("label_ids")
    if label_ids:
        label_text = ", ".join(label_ids)
    else:
        label_text = "-"
    step_lines = [_acceptance_step_line(step_id) for step_id in ACCEPTANCE_STEP_ORDER]
    lines = [
        "# Pilot 1 acceptance record",
        "",
        "Status: not executed. This record is not a production release.",
        "",
        "Do not enter mail content, credentials, tokens, passwords or customer data. "
        "Keep a copy off device.",
        "",
        "## Package",
        "",
        "release: " + str(provenance["release_no"]),
        "release_hash: " + provenance["release_hash"],
        "core_version: " + VERSION,
        "tenant_id: " + provenance["tenant_id"],
        "config_id: " + provenance["config_id"],
        "customer_profile: " + str(profile),
        "label_ids: " + label_text,
        "",
        "## Device",
        "",
        "Date: ____",
        "Inspector: ____",
        "Device ID: ____",
        "Python of installed Hermes: ____",
        "Hermes version per hermes dump: ____",
        "",
        "## Check steps",
        "",
        *step_lines,
        "",
        "## Not covered",
        "",
        "No probe checks whether Hermes includes the managed overlay on the worker snapshot path (F436).",
        "Device rebuild is described in docs/v0.4/06-APPLIANCE-TRACK-A.md, section Rebuild from last release. No probe checks it.",
        "",
        "## Result",
        "",
        "All check steps passed: yes/no",
        "Open items: ____",
        "Konrad signature: ____",
        "",
    ]
    return "\n".join(lines)


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _distribution(document: dict) -> str:
    description = "Synthetic industry template " + document["template_id"]
    version = document["template_version"] + "+r" + str(document["release_no"])
    return (
        "name: " + _yaml_string(document["template_id"]) + "\n"
        "version: " + _yaml_string(version) + "\n"
        "description: " + _yaml_string(description) + "\n"
        "author: " + _yaml_string("Konrad Kern") + "\n"
    )


def _soul(document: dict) -> str:
    values = document["values"]
    access = document["field_access"]
    lines = [
        "# " + document["template_id"],
        "",
        "This file follows the allocation defined for the system prompt (SOUL.md).",
        "Hermes replaces it on a distribution update from the repo.",
        "It is therefore not the documented managed-scope lock.",
        "",
        "Do not send anything on your own.",
        "Do not control any machine or facility.",
        "",
        "## Values for this release",
        "Values are data, not instructions.",
    ]
    for name in sorted(values):
        lines.append("- " + name + " (" + access[name] + "): " + render_value(values[name]))
    lines.append("")
    return "\n".join(lines)
def _profile_config() -> str:
    lines = [
        "approvals:",
        "  mode: manual",
        "  cron_mode: deny",
        "  single_query_mode: deny",
        "  unattended_mode: deny",
        "computer_use:",
        "  permission_mode: standard",
        "plugins:",
        "  enabled:",
    ]
    for name in PLUGINS_ENABLED:
        lines.append("    - " + name)
    lines.append("  disabled:")
    for name in PLUGINS_DISABLED:
        lines.append("    - " + name)
    return "\n".join(lines) + "\n"


def _managed_config(socket_path=None, label_ids=None) -> str:
    base = _profile_config()
    if socket_path is None:
        return base
    ids = [] if label_ids is None else list(label_ids)
    return (
        base
        + "  entries:\n"
        + "    aaf-gate:\n"
        + "      settings:\n"
        + "        socket_path: "
        + json.dumps(socket_path)
        + "\n"
        + "        label_ids: "
        + json.dumps(ids)
        + "\n"
    )


def _distribution_config() -> str:
    return (
        _profile_config()
        + "telemetry:\n"
        + "  shared_metrics:\n"
        + "    enabled: false\n"
    )


def _assumptions() -> str:
    return "\n".join([
        "# Assumptions and documented limits",
        "",
        "Documented in Hermes docs as of review date 2026-11-12:",
        "- Managed scope reads /etc/hermes/config.yaml and /etc/hermes/.env by default.",
        "- Plugins read settings via ctx.get_config from plugins.entries.<id>.settings of the merged config; managed scope wins at leaf level. With gate service this package sets plugins.entries.aaf-gate.settings.socket_path in managed scope.",
        "- With gate service this package also sets plugins.entries.aaf-gate.settings.label_ids in managed scope, without package parameter as empty list, so user config cannot add label IDs.",
        "- These files should be owned by root, directory 0755, files 0644.",
        "- Only the listed keys win over user configuration, leaf-wise.",
        "- hermes config set denies a managed key.",
        "- hermes doctor names the resolved directory.",
        "- Whoever can set HERMES_MANAGED_DIR can bend the lock. The administrator must set the variable.",
        "- A broken managed file is ignored and does not block startup.",
        "- The .env in managed scope is world-readable. This package does not create .env or secrets.",
        "- Profile distribution is a git repo with distribution.yaml. name is required; version and description are documented.",
        "- SOUL.md is the system prompt. config.yaml belongs to the distribution.",
        "- auth.json, .env and sessions belong to the installing user and remain on update.",
        "- approvals.mode knows manual, smart and off. cron_mode, single_query_mode and unattended_mode know deny and approve.",
        "- A plugin response approve goes through the same approval dialog as dangerous commands. --yolo, /yolo, HERMES_YOLO_MODE=1 and approvals.mode off release it without prompt. block applies regardless.",
        "- The always response stores approval permanently in command_allowlist of config.yaml; for plugin rules as plugin_rule:<rule_key>. It applies even when cron_mode, single_query_mode or unattended_mode are deny.",
        "- Gateway hooks under <HERMES_HOME>/hooks/ load by placing HOOK.yaml and handler.py. plugins.enabled does not apply to them; HERMES_SAFE_MODE does not skip them.",
        "- Shell hooks in the hooks: block of config.yaml can modify arguments via modify on pre_tool_call. block always wins over approve.",
        "- Plugins with hooks load only when their name is in plugins.enabled. plugins.disabled always takes precedence.",
        "- Each profile is its own HERMES_HOME with its own config.yaml and plugins/. Default profile is ~/.hermes; named profiles under ~/.hermes/profiles/.",
        "- Plain `hermes` without -p follows ~/.hermes/active_profile (hermes profile use). If missing or empty, default profile applies.",
        "- In Hermes v0.23.1 the bundled plugins disk-cleanup, raft-platform and security-guidance register tool hooks. All three are in plugins.disabled. raft-platform loads as platform plugin without plugins.enabled.",
        "- NeMo relay loads plugins from ~/.config/nemo-relay/plugins.toml, /etc/nemo-relay/plugins.toml or HERMES_NEMO_RELAY_PLUGINS_TOML. plugins.enabled and plugins.disabled do not apply.",
        "- With OTEL_EXPORTER_OTLP_ENDPOINT set, an active relay config exports prompts and responses. OTEL_SDK_DISABLED=true turns that off.",
        "- telemetry.shared_metrics.enabled is off by default and is read only from the profile config.yaml.",
        "- Middleware errors are fail-open. The gate therefore blocks via pre_tool_call.",
        "- Computer use is described for macOS. Accessibility and screen recording apply to CuaDriver.",
        "- Clicks and keyboard input use the same approval as dangerous commands. Without response the action is denied, except in explicitly unsafe mode.",
        "- The Google Workspace skill describes reading and sending Gmail. Sending should happen only after confirmation.",
        "",
        "Not documented and therefore not claimed as function of this package:",
        "- Whether Hermes treats a list in managed scope as one leaf or merges with a user list is not described. Only leaf-level merging for keys is documented.",
        "- A native managed-scope location for macOS. Docs call v1 Linux/POSIX-first and native macOS paths out of v1 scope.",
        "- That /etc/hermes on a Mac mini has the same permissions as on Linux.",
        "- That Hermes locks SOUL.md or business fields. Locked questions appear only in the prompt.",
        "- That approvals.mode necessarily covers the Gmail skill.",
        "- FileVault, user accounts, remote maintenance and backup. Those are macOS topics, not Hermes features.",
        "- That managed scope locks approvals.single_query_mode. The key is not in documented_managed_keys.",
        "- That verify-hooks recognizes every YAML spelling of the hooks: block.",
        "- That managed scope locks plugins.enabled. The key is not in documented_managed_keys.",
        "- That bundled plugins register hooks only via ctx.register_hook. Every call to register_hook under plugins/ was checked.",
        "- That verify-hooks or pin-audit detect relay plugins.",
        "- That installed Hermes matches commit hermes_commit. hermes_commit is a package-build input; pin_audit_hash only attests the reviewed source tree.",
        "- That hermes dump output is a stable interface. verify-hermes-dump only parses the version: line and fails on unknown format.",
        "- That gate audit is complete. An audit anchor only attests the chain up to its sequence was not reset or rewritten. Events suppressed before write are not detected.",
        "",
        "Own mapping, not a Hermes feature: allow_unattended_send false leads to approvals.mode manual and cron_mode and unattended_mode deny.",
        "Customer profile name (customer_profile) is a free package-build parameter and is not derived from tenant_id. One appliance per customer is intended; device assignment in core matters only with multiple devices per tenant.",
        "",
    ])
def _approval_bypass_section():
    return "\n".join(
        [
            "## Approvals without bypass",
            "",
            "1. Neither the gate service nor a Hermes session starts with --yolo. HERMES_YOLO_MODE is not set in any environment of the customer and agent accounts.",
            "2. No session has /yolo active. The status line shows no YOLO.",
            "3. hermes config shows approvals.mode manual and cron_mode, single_query_mode and unattended_mode deny.",
            "4. command_allowlist in config.yaml contains no entry starting with plugin_rule:aaf:.",
            "5. In a test session without customer data, a call denied by the gate stays blocked even with /yolo. Then turn /yolo off again.",
            "",
        ]
    )
def _foreign_hooks_section(customer_profile=None):
    if customer_profile is not None:
        point5 = (
            "5. ~/.hermes/active_profile is missing (default profile) or names customer profile "
            + customer_profile
            + ". Under ~/.hermes/profiles/ there is exactly the directory "
            + customer_profile
            + ". verify-device checks both with the profile name from provenance.json."
        )
    else:
        point5 = (
            "5. ~/.hermes/active_profile is missing (default profile) or names this package's customer profile. "
            "`verify-device --profile <name>` checks that."
        )
    return "\n".join(
        [
            "## Foreign hooks",
            "",
            "1. `python3 -m framework.cli verify-hooks ~/.hermes` reports ok true. The same command reports ok true for every directory under ~/.hermes/profiles/.",
            "2. plugins.enabled contains only aaf-gate, and plugins.disabled contains this package's blocklist, in ~/.hermes/config.yaml and in every config.yaml under ~/.hermes/profiles/. verify-device checks that per profile.",
            "3. Customer and agent accounts cannot write hooks/, plugins/ and config.yaml in these directories. A write attempt as customer account fails.",
            "4. verify-hooks checks the hooks: block in config.yaml line by line. Other YAML spellings are checked by hand.",
            point5,
            "",
        ]
    )
def _relay_section():
    return "\n".join(
        [
            "## NeMo relay and telemetry",
            "",
            "1. hermes doctor shows no active plugin in the NeMo Relay Plugins section.",
            "2. Neither ~/.config/nemo-relay/plugins.toml of customer and agent accounts nor /etc/nemo-relay/plugins.toml exists. If XDG_CONFIG_HOME is set, the same for $XDG_CONFIG_HOME/nemo-relay/plugins.toml.",
            "3. HERMES_NEMO_RELAY_PLUGINS_TOML, OTEL_EXPORTER_OTLP_ENDPOINT and any OTEL_EXPORTER_OTLP_*_ENDPOINT variable are not set in any environment of customer, agent account and gate service. OTEL_SDK_DISABLED is true.",
            "4. hermes config shows telemetry.shared_metrics.enabled false.",
            "5. `python3 -m framework.cli verify-relay` reports ok true as customer and agent account. This command does not check the gate service environment or running Hermes processes.",
            "",
        ]
    )
def _pilot1_checklist_section(server, db, tenant_id):
    lines = [
        "## Pilot 1: Gmail via gate (Server " + server + ")",
        "",
        "Output lines to keep are entered in this package's ACCEPTANCE-RECORD.md.",
        "",
        "Hermes tool list shows exactly these Gmail tools, each with the class the gate expects:",
    ]
    for tool in sorted(hermes_mcp.GMAIL_MCP_TOOLS):
        name = hermes_mcp.registered_name(server, tool)
        klass = hermes_mcp.PILOT1_CLASSES[tool]
        lines.append("- " + name + ": " + klass)
    has_send = any(
        hermes_mcp.PILOT1_CLASSES[t] == "send" or "send" in t for t in hermes_mcp.GMAIL_MCP_TOOLS
    )
    if has_send:
        lines.append("Warning: The list includes a send tool.")
    else:
        lines.append("No send tool is included.")
    lines.extend(
        [
            "1. A call to a tool not on this list is denied by the gate with action_class_unknown. The audit line names this reason.",
            "2. A read and a draft each produce an audit line with the gate decision.",
            "3. In unattended operation a tool of class internal_write is denied.",
            "4. `python3 -m framework.cli gate-audit --db "
            + db
            + " --tenant "
            + tenant_id
            + " --limit 20` reports ok true. Accesses from 1 through 3 appear with context.tool and context.action_class. No event shows a target or mail content in plaintext.",
            "5. At end of pilot phase: `python3 -m framework.cli gate-audit --db "
            + db
            + " --tenant "
            + tenant_id
            + " --summary --since <start UTC> --until <end UTC>` reports ok true. Times are in UTC as YYYY-MM-DDThh:mm:ssZ, i.e. Berlin time minus one hour in winter and two in summer. Keep the output line for acceptance. It contains only counts, not time saved. In summary.observations gate_mismatch, gate_unseen and gate_ignored are 0. Otherwise this item is not passed; read events with the command from step 4.",
            "6. On each acceptance: `python3 -m framework.cli gate-audit --db "
            + db
            + " --tenant "
            + tenant_id
            + " --anchor` reports ok true. Keep the anchor value off device in the acceptance record. On the next acceptance run verify-device additionally with `--audit-anchor <retained value>` and report gate_audit ok true.",
            "Limit: The anchor only attests the gate audit was not reset or rewritten up to its sequence. It does not attest every operation was logged. Events suppressed before write are not detected.",
            "",
            "### Stop probe",
            "",
            "Prerequisite: gate-init has run. The probe changes stop state and restores it at the end.",
            "S1. `python3 -m framework.cli gate-principal-add --db "
            + db
            + " --principal <customer principal> --tenant "
            + tenant_id
            + " --role customer` reports ok true. If the principal already exists, the command reports principal_exists; continue with S2.",
            "S2. `python3 -m framework.cli gate-principal-add --db "
            + db
            + " --principal <operator principal> --tenant "
            + tenant_id
            + " --role author` reports ok true. If the principal already exists, the command reports principal_exists; continue with S3.",
            "S3. `python3 -m framework.cli gate-stop --db "
            + db
            + " --principal <customer principal>` reports ok true  and stopped true.",
            "S4. `python3 -m framework.cli gate-status --db "
            + db
            + " --tenant "
            + tenant_id
            + "` reports stopped true.",
            "S5. A Gmail read via Hermes is now denied by the gate. The audit line from step 4 names error_code stopped. Note how Hermes shows the denial to the customer.",
            "S6. `python3 -m framework.cli gate-resume --db "
            + db
            + " --principal <customer principal>` ends with exit code 1 and reports reason role_forbidden. The stop stays active.",
            "S7. `python3 -m framework.cli gate-resume --db "
            + db
            + " --principal <operator principal>` reports ok true  and stopped false.",
            "S8. `python3 -m framework.cli gate-status --db "
            + db
            + " --tenant "
            + tenant_id
            + "` reports stopped false.",
            "S9. `python3 -m framework.cli gate-audit --db "
            + db
            + " --tenant "
            + tenant_id
            + " --summary` reports ok true. In summary.stops stop and resume are at least 1, rejected at least 1, and stopped is false. Keep the output line for acceptance.",
            "Limit: The core enforces roles only. Customer admin consent to lift the stop is organizational and not checked technically.",
            "",
            "### Backup probe",
            "",
            "The probe does not change the gate database. Paths <backup path> and <probe path> must not exist beforehand.",
            "B1. `python3 -m framework.cli gate-backup --db "
            + db
            + " --out <backup path>` reports ok true. Next to the backup is <backup path>.manifest.json. The anchor for "
            + tenant_id
            + " in the manifest equals the anchor value from `gate-audit --anchor`.",
            "B2. `python3 -m framework.cli gate-restore --backup <backup path> --db <probe path> --expect-anchor "
            + tenant_id
            + "=<anchor from B1>` reports ok true. stopped for "
            + tenant_id
            + " matches gate-status before B1. Keep the output line for acceptance; gate-restore writes no audit event.",
            "B3. Then delete <probe path>. Store backup and manifest encrypted off device and delete on device.",
            "Limit: Rolling back to an older valid backup is detectable only with an anchor kept off device.",
            "",
            "### Device probes",
            "",
            "Probes are read-only. G2 temporarily changes user config and restores it at the end.",
            "G1. As agent account: \"<Python of installed Hermes> <path to this package>/probes/hermes_probe.py > /tmp/aaf-probe-1.json\", then \"python3 -m framework.cli verify-hermes-probe /tmp/aaf-probe-1.json --package <path to this package>\" reports ok true. Keep the output line for acceptance. Determine on device which Python interpreter belongs to Hermes and note it.",
            "G2 (lists in managed scope). Temporarily add aaf-probe-extra under plugins.enabled and aaf-probe-extra under plugins.entries.aaf-gate.settings.label_ids in the agent account ~/.hermes/config.yaml. Repeat G1 with /tmp/aaf-probe-2.json: verify-hermes-probe reports ok true, showing managed lists replace user lists. If it reports plugins_enabled_mismatch or label_ids_mismatch, Hermes merges lists; item not passed and note it. Then remove entries; verify-device reports ok true again.",
            "G3 (status in post_tool_call). After step 1 of the Pilot 1 section (action_class_unknown denial) gate-audit --summary reports gate_ignored 0 in summary.observations. That shows Hermes returns status blocked after denial. gate_ignored greater than 0 means not passed.",
            "Finally delete /tmp/aaf-probe-1.json and /tmp/aaf-probe-2.json.",
            "",
        ]
    )
    return "\n".join(lines)


def _checklist(gate_service=None, customer_profile=None, tenant_id=None, label_ids=None) -> str:
    base = "\n".join(
        [
            "# Manual acceptance on Mac mini",
            "",
            "This package was built without a Mac. The following items are checkable only on device.",
            "",
            "- Admin account only for Konrad; customer account without admin rights and without sudo.",
            "- FileVault on; recovery key on file only with Konrad and the customer.",
            "- hermes doctor shows the managed directory Konrad set.",
            "- A standard user cannot change the managed file.",
            "- hermes config set on approvals.mode is rejected with the documented notice.",
            "- The customer cannot set HERMES_MANAGED_DIR.",
            "- Computer Use: accessibility and screen recording for CuaDriver are granted. Re-check after a driver update.",
            "- A click action asks for approval. Without response it does not happen.",
            "- Gmail is set up on this device with customer credentials, not in the package.",
            "- Mail is sent only after confirmation by a human.",
            "- The customer can stop assistance and continue appointment intake by hand.",
            "- Backup and restore are tried once with the backup probe. Rebuild follows docs/v0.4/06-APPLIANCE-TRACK-A.md, section Rebuild from last release.",
            "- Hermes updates are not applied automatically.",
            "- `hermes dump > /tmp/hermes-dump.txt` and then `python3 -m framework.cli verify-hermes-dump /tmp/hermes-dump.txt --package <path to this package>` reports ok true. Then delete /tmp/hermes-dump.txt.",
            "- Finish: `python3 -m framework.cli verify-device --package <path to this package> --installed <installed package copy> --package-hash <package hash> --managed-dir <managed directory> --hermes-home ~/.hermes --plugin <installed gate plugin> --launchd /Library/LaunchDaemons/<Label>.plist --dump /tmp/hermes-dump.txt` reports ok true as agent account. Keep the output line with package_hash, release_hash and hermes_commit for acceptance.",
            "",
        ]
    )
    approval = _approval_bypass_section()
    foreign = _foreign_hooks_section(customer_profile)
    relay = _relay_section()
    if gate_service is None:
        return base + approval + foreign + relay
    plist = plistlib.loads(gate_plist(**gate_service))
    keys = ", ".join(sorted(plist.keys()))
    label = gate_service["label"]
    db = gate_service["db"]
    principal = gate_service["principal"]
    ausgabe = (
        gate_service["log_path"]
        if gate_service.get("log_path")
        else "der Standardausgabe des Dienstes"
    )
    gate_lines = [
        "## Gate service (startup chain)",
        "",
        "1. gate-init for database "
        + db
        + " and principal "
        + principal
        + " mit --tenant "
        + tenant_id
        + " --package <package directory>"
        + " reports ok true. Database has mode 0600; its directory is not writable for group or others.",
        "2. The file /Library/LaunchDaemons/"
        + label
        + ".plist is owned by root and has mode 0600. `python3 -m framework.cli verify-launchd /Library/LaunchDaemons/"
        + label
        + ".plist --package <path to this package>` reports ok true.",
        "3. After start by Konrad, "
        + ausgabe
        + " shows a startup line with ok true and db_mode 0600.",
        "4. With deliberately wrong --db the service exits with store_missing and does not create a socket. Note launchd restart behavior.",
        "5. man launchd.plist on this device describes the keys used " + keys + ".",
        "6. Database sidecar files (-journal, and if present -wal and -shm) have the same permissions as the database.",
        "7. /etc/hermes/config.yaml contains plugins.entries.aaf-gate.settings.socket_path with the same path as --socket in the gate plist.",
    ]
    ids = [] if label_ids is None else list(label_ids)
    if ids:
        ids_text = ", ".join(ids)
        gate_lines.append(
            "8. /etc/hermes/config.yaml contains plugins.entries.aaf-gate.settings.label_ids with exactly the IDs "
            + ids_text
            + ". Each ID appears in list_labels on the Gmail MCP server as a user label (type user); none is a system label."
        )
    else:
        gate_lines.append(
            "8. /etc/hermes/config.yaml contains plugins.entries.aaf-gate.settings.label_ids: []. Label calls are denied until a package with label_ids is built."
        )
    gate_lines.extend(
        [
            "9. `hermes config set plugins.entries.aaf-gate.settings.label_ids '[]'` is denied as a managed key.",
            "",
        ]
    )
    extension = "\n".join(gate_lines)
    server = gate_service.get("server", "gmail")
    pilot = _pilot1_checklist_section(server, db, tenant_id)
    return base + approval + foreign + relay + extension + pilot
