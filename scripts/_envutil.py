"""Shared helpers for scripts that read/write the project .env file.

The .env file holds runtime environment variables for local development
(e.g. DSG_URL, OWNER, SIG_BUCKET) plus deploy-time settings used by
scripts/deploy.py (GCP_PROJECT, GCP_REGION, SERVICE_NAME). Both setup
and deploy share the same file so values entered once are reused.
"""

from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Load key=value pairs from a .env file."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def save_env(env: dict[str, str], path: Path = ENV_FILE) -> None:
    """Save key=value pairs to a .env file, preserving insertion order."""
    lines = [f"{k}={v}" for k, v in env.items() if v != ""]
    path.write_text("\n".join(lines) + "\n")
    print(f"  Settings saved to {path}")


def prompt_value(label: str, default: str, optional: bool) -> str:
    """Prompt for a value with an optional default; returns the chosen value."""
    if default:
        hint = f" [{default}]"
    elif optional:
        hint = " (optional, Enter to skip)"
    else:
        hint = ""
    value = input(f"  {label}{hint}: ").strip()
    if not value:
        return default
    return value


def collect_values(
    saved: dict[str, str],
    settings: list[tuple[str, str, str, bool]],
    use_env: bool,
) -> dict[str, str]:
    """Resolve values for ``settings`` from saved values and/or prompts.

    Each setting is (key, label, default, is_optional). When ``use_env``
    is True, only keys missing from ``saved`` are prompted; otherwise
    every key is prompted with the saved value as default.
    """
    resolved: dict[str, str] = {}
    for key, label, default, optional in settings:
        existing = saved.get(key, default)
        if use_env and existing:
            resolved[key] = existing
            continue
        value = prompt_value(label, existing, optional)
        if not value and not optional:
            raise SystemExit(f"Error: {label} is required")
        resolved[key] = value
    return resolved
