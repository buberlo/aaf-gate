from pathlib import Path


def verify_relay(environ, home, system_root="/") -> list[str]:
    issues: list[str] = []
    relay_toml = environ.get("HERMES_NEMO_RELAY_PLUGINS_TOML", "")
    if relay_toml.strip():
        issues.append("relay_toml_env")
    for name, value in environ.items():
        if name.startswith("OTEL_EXPORTER_OTLP_") and name.endswith("ENDPOINT"):
            if value.strip():
                issues.append("otlp_endpoint:" + name)
    if environ.get("OTEL_SDK_DISABLED", "").strip().lower() != "true":
        issues.append("otel_sdk_not_disabled")
    home_path = Path(home)
    candidates = [home_path / ".config" / "nemo-relay" / "plugins.toml"]
    xdg = environ.get("XDG_CONFIG_HOME", "")
    if xdg.strip():
        candidates.append(Path(xdg) / "nemo-relay" / "plugins.toml")
    candidates.append(Path(system_root) / "etc" / "nemo-relay" / "plugins.toml")
    for path in candidates:
        if path.exists():
            issues.append("relay_plugins_file:" + str(path))
    return sorted(set(issues))