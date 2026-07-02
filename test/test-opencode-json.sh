#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${ROOT}/image/.opencode/opencode.json"

[ -f "${CONFIG}" ] || { echo "missing ${CONFIG}" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 required" >&2; exit 1; }

# opencode.json is JSONC (JSON-with-Comments): it legitimately uses //
# line comments and trailing commas (like a VS Code config). Strict JSON
# parsers (jq, json.loads) reject those, so they are NOT a valid check here.
# Strip comments (string-aware) and trailing commas, then validate as JSON.
python3 - "${CONFIG}" <<'PY'
import json
import re
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()

out: list[str] = []
i, n, in_str = 0, len(text), False
while i < n:
    c = text[i]
    if in_str:
        out.append(c)
        if c == "\\" and i + 1 < n:  # preserve escaped char verbatim
            out.append(text[i + 1])
            i += 2
            continue
        if c == '"':
            in_str = False
        i += 1
        continue
    if c == '"':
        in_str = True
        out.append(c)
        i += 1
        continue
    if c == "/" and i + 1 < n and text[i + 1] in ("/", "*"):
        if text[i + 1] == "/":  # line comment -> end of line
            while i < n and text[i] != "\n":
                i += 1
        else:  # block comment -> closing */
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
        continue
    out.append(c)
    i += 1

cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
try:
    json.loads(cleaned)
except json.JSONDecodeError as e:
    print(
        f"opencode.json: invalid JSONC ({e.msg} at line {e.lineno} col {e.colno})",
        file=sys.stderr,
    )
    sys.exit(1)
PY

grep -q '"default_agent"' "${CONFIG}"
grep -q '"remote"' "${CONFIG}"
echo "opencode.json: ok"
