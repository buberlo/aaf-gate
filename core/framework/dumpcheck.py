import re

DUMP_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")
PROVENANCE_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
BRACKET_PATTERN = re.compile(r"\[([^\]]*)\]\s*$")


def verify_dump_commit(dump_text: str, hermes_commit: str | None) -> list[str]:
    issues: list[str] = []
    dump_commit: str | None = None
    found_line = False
    for line in dump_text.splitlines():
        if not line.lstrip().startswith("version:"):
            continue
        found_line = True
        match = BRACKET_PATTERN.search(line)
        if not match:
            issues.append("dump_version_missing")
            break
        inner = match.group(1).strip()
        if not inner:
            issues.append("dump_version_missing")
            break
        if DUMP_COMMIT_PATTERN.fullmatch(inner) is None:
            issues.append("dump_commit_unknown")
        else:
            dump_commit = inner
        break
    if not found_line:
        issues.append("dump_version_missing")
    prov_ok = (
        isinstance(hermes_commit, str)
        and PROVENANCE_COMMIT_PATTERN.fullmatch(hermes_commit) is not None
    )
    if not prov_ok:
        issues.append("provenance_commit_missing")
    if dump_commit is not None and prov_ok and not hermes_commit.startswith(dump_commit):
        issues.append("commit_mismatch")
    return sorted(set(issues))