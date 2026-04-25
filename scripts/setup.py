#!/usr/bin/env python3
"""Configure runtime environment variables for clio-store.

Writes .env with the values clio-store reads at startup. By default
each key is prompted with the current .env value as the default, so
re-running just lets you accept everything with Enter. With
``--use-env``, only keys missing from .env are prompted.

Usage:
    pixi run setup
    pixi run setup --use-env
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _envutil import ENV_FILE, collect_values, load_env, save_env

# (key, label, default, is_optional)
RUNTIME_SETTINGS: list[tuple[str, str, str, bool]] = [
    ("DSG_URL", "DatasetGateway URL (DSG_URL)", "", False),
    ("OWNER", "Admin email (OWNER)", "", False),
    ("URL_PREFIX", "API URL prefix (URL_PREFIX)", "", True),
    ("ALLOWED_ORIGINS", "CORS allowed origins (ALLOWED_ORIGINS)", "*", True),
    ("SIG_BUCKET", "Image-signature GCS bucket (SIG_BUCKET)", "", True),
    ("TRANSFER_FUNC", "Image transfer Cloud Function URL (TRANSFER_FUNC)", "", True),
    ("TRANSFER_DEST", "Image transfer cache location (TRANSFER_DEST)", "", True),
    ("GOOGLE_APPLICATION_CREDENTIALS",
     "Path to GCP service-account JSON (GOOGLE_APPLICATION_CREDENTIALS)", "", True),
]


def main():
    parser = argparse.ArgumentParser(description="Configure clio-store runtime env vars")
    parser.add_argument(
        "--use-env",
        action="store_true",
        help="Use existing .env values; only prompt for keys that are missing",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("clio-store — Local environment setup")
    print("=" * 60)
    if args.use_env:
        print(f"\nUsing values from {ENV_FILE} where present; prompting for missing keys.\n")
    else:
        print(f"\nPress Enter to keep the current value shown in [brackets].\n")

    saved = load_env()
    resolved = collect_values(saved, RUNTIME_SETTINGS, use_env=args.use_env)

    # Preserve any other keys already in .env (e.g. deploy-only keys
    # written by scripts/deploy.py: GCP_PROJECT, GCP_REGION, SERVICE_NAME).
    merged = dict(saved)
    merged.update(resolved)

    save_env(merged)


if __name__ == "__main__":
    main()
