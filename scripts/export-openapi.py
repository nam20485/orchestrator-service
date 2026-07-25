#!/usr/bin/env python3
"""Export the webhook_receiver FastAPI OpenAPI schema to docs/openapi.json.

Builds the app with a fixed, deterministic configuration instead of
Settings.from_env() (which requires OS_WEBHOOK_SECRET and varies by
environment) so the exported schema -- and which routes it contains, since
the simulator/dashboard register different stub routes when disabled -- does
not depend on the developer's environment. All values below are placeholders,
never real credentials.

Usage:
    uv run python scripts/export-openapi.py           # write docs/openapi.json
    uv run python scripts/export-openapi.py --check    # exit 1 if stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from webhook_receiver.app import create_app
from webhook_receiver.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "docs" / "openapi.json"


def _fixed_settings() -> Settings:
    return Settings(
        host="0.0.0.0",
        port=8080,
        github_webhook_secret="PLACEHOLDER-SCHEMA-EXPORT-SECRET",
        opencode_server_url="http://orchestratorservice:4099",
        prompt_script=REPO_ROOT / "scripts" / "prompt.ps1",
        workspace="/workspace",
        model="zai-coding-plan/glm-5",
        agent="orchestrator",
        max_payload_chars=120000,
        max_body_bytes=25 * 1024 * 1024,
        log_level="info",
        enable_simulator=True,
        beads_enabled=False,
        beads_poll_interval=10,
        beads_max_retries=3,
        beads_workspace_root="/workspace",
        dashboard_token="PLACEHOLDER-SCHEMA-EXPORT-TOKEN",
    )


def render_schema() -> str:
    app = create_app(_fixed_settings())
    schema = app.openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if docs/openapi.json is stale instead of (re)writing it.",
    )
    args = parser.parse_args()

    rendered = render_schema()

    if args.check:
        if not OUTPUT_PATH.exists():
            print(
                f"{OUTPUT_PATH} does not exist. Run: uv run python scripts/export-openapi.py",
                file=sys.stderr,
            )
            return 1
        current = OUTPUT_PATH.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"{OUTPUT_PATH} is stale. Re-run: uv run python scripts/export-openapi.py, "
                "then commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT_PATH} is up to date.")
        return 0

    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
