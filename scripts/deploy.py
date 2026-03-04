#!/usr/bin/env python3
"""Deploy clio-store to Google Cloud Run.

Interactively prompts for deployment settings (GCP project, region, env vars),
saves them to .env for reuse, and runs gcloud run deploy.

Usage:
    pixi run deploy
    pixi run deploy --dry-run
"""

import argparse
import subprocess
import sys
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# Deployment settings: (env_key, prompt_label, default_value, is_optional)
DEPLOY_SETTINGS = [
    ("GCP_PROJECT", "GCP Project ID", "", False),
    ("GCP_REGION", "GCP Region", "us-east4", False),
    ("SERVICE_NAME", "Cloud Run service name", "clio-store", False),
]

# Environment variables to set on the Cloud Run service.
# Prompted in order; NEUPRINT_APPLICATION_CREDENTIALS and FLYEM_SECRET are
# skipped when DSG_URL is set (neuPrintHTTP uses DSG tokens directly, and
# FlyEM JWT is part of legacy auth).
SERVICE_ENV_VARS = [
    ("OWNER", "Admin email (OWNER)", "", True),
    ("DSG_URL", "DatasetGateway URL (DSG_URL)", "", True),
    ("URL_PREFIX", "API URL prefix (URL_PREFIX)", "", True),
    ("ALLOWED_ORIGINS", "CORS allowed origins (ALLOWED_ORIGINS)", "*", True),
    ("SIG_BUCKET", "Signature query GCS bucket (SIG_BUCKET)", "", True),
    ("TRANSFER_FUNC", "Transfer cloud run location (TRANSFER_FUNC)", "", True),
    ("TRANSFER_DEST", "Transfer cache location (TRANSFER_DEST)", "", True),
]

# Only prompted when DSG_URL is not set (legacy mode)
LEGACY_ENV_VARS = [
    ("NEUPRINT_APPLICATION_CREDENTIALS", "Neuprint credentials (NEUPRINT_APPLICATION_CREDENTIALS)", "", True),
    ("FLYEM_SECRET", "FlyEM JWT secret (FLYEM_SECRET)", "", True),
]


def load_env(path: Path) -> dict[str, str]:
    """Load key=value pairs from a .env file."""
    env = {}
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


def save_env(path: Path, env: dict[str, str]) -> None:
    """Save key=value pairs to a .env file."""
    lines = [f"{k}={v}" for k, v in env.items()]
    path.write_text("\n".join(lines) + "\n")
    print(f"\n  Settings saved to {path}")


def prompt_value(label: str, default: str, optional: bool) -> str:
    """Prompt the user for a value, showing the default."""
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


def run_cmd(cmd: list[str], check: bool = True, capture: bool = True,
            silent: bool = False) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    if not silent:
        print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if check and result.returncode != 0:
        print(f"Error: Command failed with exit code {result.returncode}")
        if result.stderr:
            print(result.stderr)
        sys.exit(1)
    return result


def check_prerequisites() -> str:
    """Validate gcloud CLI and auth. Returns the authenticated account."""
    result = run_cmd(["which", "gcloud"], check=False, silent=True)
    if result.returncode != 0:
        print("Error: gcloud CLI not found")
        print("Install from: https://cloud.google.com/sdk/docs/install")
        sys.exit(1)
    print("  gcloud CLI: OK")

    result = run_cmd(
        ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        check=False, silent=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        print("Error: gcloud not authenticated. Run: gcloud auth login")
        sys.exit(1)
    account = result.stdout.strip().split("\n")[0]
    print(f"  Authenticated as: {account}")
    return account


def get_service_url(service_name: str, region: str, project_id: str) -> str | None:
    """Get the URL of a deployed Cloud Run service."""
    result = run_cmd(
        ["gcloud", "run", "services", "describe", service_name,
         f"--region={region}", f"--project={project_id}",
         "--format=value(status.url)"],
        check=False, silent=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def main():
    parser = argparse.ArgumentParser(description="Deploy clio-store to Cloud Run")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the deploy command without executing")
    args = parser.parse_args()

    print("=" * 60)
    print("clio-store — Cloud Run Deployment")
    print("=" * 60)

    # Check prerequisites
    print("\n[1/3] Checking prerequisites...")
    check_prerequisites()

    # Load saved settings
    saved = load_env(ENV_FILE)

    # Prompt for deployment settings
    print("\n[2/3] Deployment settings (press Enter to keep defaults)...\n")
    env = {}

    for key, label, default, optional in DEPLOY_SETTINGS:
        env[key] = prompt_value(label, saved.get(key, default), optional)
        if not optional and not env[key]:
            print(f"Error: {label} is required")
            sys.exit(1)

    print()
    for key, label, default, optional in SERVICE_ENV_VARS:
        env[key] = prompt_value(label, saved.get(key, default), optional)

    # Only prompt for legacy-mode vars when DSG_URL is not set
    all_env_keys = [k for k, _, _, _ in SERVICE_ENV_VARS]
    if env.get("DSG_URL"):
        print("\n  (DSG_URL is set — skipping legacy auth settings)\n")
    else:
        print()
        for key, label, default, optional in LEGACY_ENV_VARS:
            env[key] = prompt_value(label, saved.get(key, default), optional)
        all_env_keys += [k for k, _, _, _ in LEGACY_ENV_VARS]

    # Save for next time
    save_env(ENV_FILE, env)

    # Build the gcloud command
    project = env["GCP_PROJECT"]
    region = env["GCP_REGION"]
    service = env["SERVICE_NAME"]

    # Collect non-empty service env vars
    env_var_pairs = []
    for key in all_env_keys:
        if env.get(key):
            env_var_pairs.append(f"{key}={env[key]}")

    deploy_cmd = [
        "gcloud", "run", "deploy", service,
        "--source=.",
        f"--project={project}",
        f"--region={region}",
        "--allow-unauthenticated",
        "--use-http2",
    ]
    if env_var_pairs:
        deploy_cmd.append(f"--set-env-vars={','.join(env_var_pairs)}")

    # Deploy
    print(f"\n[3/3] Deploying to Cloud Run...")
    print(f"  Project: {project}")
    print(f"  Region:  {region}")
    print(f"  Service: {service}")
    if env_var_pairs:
        print(f"  Env vars: {', '.join(k.split('=')[0] for k in env_var_pairs)}")
    print()

    if args.dry_run:
        print("[DRY RUN] Would execute:\n")
        print(f"  {' '.join(deploy_cmd)}\n")
        print("Run without --dry-run to deploy.")
        return

    run_cmd(deploy_cmd, capture=False)

    # Show result
    service_url = get_service_url(service, region, project)
    print("\n" + "=" * 60)
    print("Deployment complete!")
    print("=" * 60)
    if service_url:
        print(f"\nService URL: {service_url}")
    else:
        print("\nCheck the Cloud Run console for the service URL.")


if __name__ == "__main__":
    main()
