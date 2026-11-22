import hashlib
import json
import os
import stat
from pathlib import Path

from framework.configplane import ConfigError

INSTALL_MAP_SOURCE = (
    "https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/"
    "BPSystemStartup/Chapters/CreatingLaunchdJobs.html"
)


def verify_launchd_file(path, expected_sha256, expected_uid=0):
    try:
        st = os.lstat(path)
    except OSError:
        return ["missing"]
    if stat.S_ISLNK(st.st_mode):
        return ["symlink"]
    if not stat.S_ISREG(st.st_mode):
        return ["missing"]
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ["missing"]
    issues = []
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        issues.append("sha256_mismatch")
    if st.st_uid != expected_uid:
        issues.append("owner_not_root")
    if stat.S_IMODE(st.st_mode) not in (0o600, 0o400):
        issues.append("mode_too_open")
    return sorted(set(issues))


def package_launchd_expectation(package_dir):
    provenance_path = Path(package_dir) / "provenance.json"
    data = json.loads(provenance_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError("GATE_SERVICE_ABSENT")
    label = data.get("gate_launchd_label")
    digest = data.get("gate_plist_sha256")
    if not isinstance(label, str) or not label:
        raise ConfigError("GATE_SERVICE_ABSENT")
    if not isinstance(digest, str) or not digest:
        raise ConfigError("GATE_SERVICE_ABSENT")
    return label, digest


def install_map_body(label):
    return {
        "entries": [
            {
                "mode": "0600",
                "owner": "root",
                "package_path": "launchd/" + label + ".plist",
                "source": INSTALL_MAP_SOURCE,
                "target": "/Library/LaunchDaemons/" + label + ".plist",
            }
        ],
        "unmapped": "other package files have no documented device target yet",
    }