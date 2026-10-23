"""Deploy to AWS via Serverless Framework, loading secrets from .env safely.

Never source .env in bash for this - unquoted values containing braces/
commas/quotes (e.g. GOOGLE_SHEETS_CREDENTIALS_JSON) get silently mangled
by bash's own parsing (brace expansion, quote stripping). This uses
python-dotenv to pass values through untouched.

Usage:
    .venv/bin/python scripts/deploy.py [--stage dev]
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AWS_PROFILE = "billbolo"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default="dev")
    args = parser.parse_args()

    env = os.environ.copy()
    env.update(dotenv_values(PROJECT_ROOT / ".env"))

    result = subprocess.run(
        [
            "npx",
            "serverless",
            "deploy",
            "--stage",
            args.stage,
            "--aws-profile",
            AWS_PROFILE,
        ],
        cwd=PROJECT_ROOT,
        env=env,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
