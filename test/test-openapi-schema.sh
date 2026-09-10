#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# docs/openapi.json is a committed artifact (scripts/export-openapi.py); a
# route change without regenerating it should fail here rather than drift
# silently.
uv run python scripts/export-openapi.py --check
