#!/usr/bin/env python3
"""Deploy clio-store to Google Cloud Run.

Interactively prompts for deployment settings (GCP project, region, env vars),
saves them to .env for reuse, and runs gcloud run deploy. Runtime env vars
default to whatever was set via `pixi run setup`.

Usage:
    pixi run deploy
    pixi run deploy --dry-run
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _envutil import ENV_FILE, collect_values, load_env, save_env

# Deploy-only settings (where to push). (key, label, default, is_optional)
DEPLOY_SETTINGS: list[tuple[str, str, str, bool]] = [
    ("GCP_PROJECT", "GCP Project ID", "", False),
    ("GCP_REGION", "GCP Region", "us-east4", False),
    ("SERVICE_NAME", "Cloud Run service name", "clio-store", False),
]

# Runtime env vars that get baked into the Cloud Run service.
SERVICE_ENV_VARS: list[tuple[str, str, str, bool]] = [
    ("DSG_URL", "DatasetGateway URL (DSG_URL)", "", False),
    ("OWNER", "Admin email (OWNER)", "", False),
    ("URL_PREFIX", "API URL prefix (URL_PREFIX)", "", True),
    ("ALLOWED_ORIGINS", "CORS allowed origins (ALLOWED_ORIGINS)", "*", True),
    ("SIG_BUCKET", "Signature query GCS bucket (SIG_BUCKET)", "", True),
    ("TRANSFER_FUNC", "Transfer cloud run location (TRANSFER_FUNC)", "", True),
    ("TRANSFER_DEST", "Transfer cache location (TRANSFER_DEST)", "", True),
]


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

    print("\n[1/3] Checking prerequisites...")
    check_prerequisites()

    saved = load_env()

    print("\n[2/3] Deployment settings (press Enter to keep defaults)...\n")
    deploy_env = collect_values(saved, DEPLOY_SETTINGS, use_env=False)

    print()
    service_env = collect_values(saved, SERVICE_ENV_VARS, use_env=False)

    merged = dict(saved)
    merged.update(deploy_env)
    merged.update(service_env)
    save_env(merged)

    project = deploy_env["GCP_PROJECT"]
    region = deploy_env["GCP_REGION"]
    service = deploy_env["SERVICE_NAME"]

    env_var_pairs = [
        f"{key}={service_env[key]}"
        for key, _, _, _ in SERVICE_ENV_VARS
        if service_env.get(key)
    ]

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

    print(f"\n[3/3] Deploying to Cloud Run...")
    print(f"  Project: {project}")
    print(f"  Region:  {region}")
    print(f"  Service: {service}")
    if env_var_pairs:
        print(f"  Env vars: {', '.join(p.split('=')[0] for p in env_var_pairs)}")
    print()

    if args.dry_run:
        print("[DRY RUN] Would execute:\n")
        print(f"  {' '.join(deploy_cmd)}\n")
        print("Run without --dry-run to deploy.")
        return

    run_cmd(deploy_cmd, capture=False)

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
