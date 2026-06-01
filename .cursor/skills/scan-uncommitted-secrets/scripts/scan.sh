#!/usr/bin/env bash
# Scan staged, unstaged, and untracked (non-ignored) files for sensitive content.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

mapfile -t FILES < <(
  {
    git diff --name-only --diff-filter=ACMRTUXB HEAD 2>/dev/null || true
    git diff --cached --name-only --diff-filter=ACMRTUXB 2>/dev/null || true
    git ls-files --others --exclude-standard 2>/dev/null || true
  } | sort -u
)

if [ "${#FILES[@]}" -eq 0 ] || [ -z "${FILES[0]:-}" ]; then
  echo "No changed or untracked files to scan."
  exit 0
fi

FINDINGS=0

is_binary() {
  local f="$1"
  file -b --mime-type "$f" 2>/dev/null | grep -q '^text/' || return 0
  return 1
}

should_skip_file() {
  local f="$1"
  [[ "$f" == *"/scan-uncommitted-secrets/"* ]] && return 0
  git check-ignore -q "$f" 2>/dev/null && return 0
  [[ ! -f "$f" ]] && return 0
  is_binary "$f" && return 0
  return 1
}

is_allowlisted_line() {
  local line="$1"
  [[ "$line" =~ FAKE-KEY-FOR-TESTING ]] && return 0
  [[ "$line" =~ example\.(com|org|net) ]] && return 0
  [[ "$line" =~ @example\. ]] && return 0
  [[ "$line" =~ your-.*-key ]] && return 0
  [[ "$line" =~ \<YOUR_.*\> ]] && return 0
  [[ "$line" =~ changeme|placeholder|redacted|REDACTED ]] && return 0
  return 1
}

report() {
  local category="$1"
  local file="$2"
  local line_no="$3"
  local rule="$4"
  local line="$5"

  if [ "$FINDINGS" -eq 0 ]; then
    echo ""
    echo "======================================================================"
    echo "  WARNING: SENSITIVE CONTENT DETECTED — DO NOT COMMIT"
    echo "======================================================================"
    echo ""
  fi
  FINDINGS=$((FINDINGS + 1))
  echo "[$category] $file:$line_no"
  echo "  rule: $rule"
  echo "  line: $line"
  echo ""
}

scan_file() {
  local f="$1"
  local line_no=0
  local line

  while IFS= read -r line || [ -n "$line" ]; do
    line_no=$((line_no + 1))
    is_allowlisted_line "$line" && continue

    if [[ "$line" =~ (AKIA[0-9A-Z]{16}) ]]; then
      report "API_KEY" "$f" "$line_no" "AWS access key id" "$line"
    fi

    if [[ "$line" =~ (ASIA[0-9A-Z]{16}) ]]; then
      report "API_KEY" "$f" "$line_no" "AWS temporary access key id" "$line"
    fi

    if [[ "$line" =~ (sk-or-v1-[a-zA-Z0-9_-]{10,}) ]]; then
      report "API_KEY" "$f" "$line_no" "OpenRouter/OpenAI-style key" "$line"
    fi

    if [[ "$line" =~ (sk-[a-zA-Z0-9]{20,}) ]]; then
      report "API_KEY" "$f" "$line_no" "Secret key (sk- prefix)" "$line"
    fi

    if [[ "$line" =~ (ghp_[a-zA-Z0-9]{20,}) ]]; then
      report "TOKEN" "$f" "$line_no" "GitHub personal access token" "$line"
    fi

    if [[ "$line" =~ (ghs_[a-zA-Z0-9]{20,}) ]]; then
      report "TOKEN" "$f" "$line_no" "GitHub secret/token" "$line"
    fi

    if [[ "$line" =~ (github_pat_[a-zA-Z0-9_]{20,}) ]]; then
      report "TOKEN" "$f" "$line_no" "GitHub fine-grained PAT" "$line"
    fi

    if [[ "$line" =~ (xox[baprs]-[a-zA-Z0-9-]{10,}) ]]; then
      report "TOKEN" "$f" "$line_no" "Slack token" "$line"
    fi

    if [[ "$line" =~ (Bearer[[:space:]]+[a-zA-Z0-9\-._~+/]{20,}) ]]; then
      report "TOKEN" "$f" "$line_no" "Bearer token" "$line"
    fi

    if [[ "$line" =~ -----BEGIN[[:space:]](RSA[[:space:]]|OPENSSH[[:space:]]|EC[[:space:]]|)PRIVATE[[:space:]]KEY----- ]]; then
      report "SECRET" "$f" "$line_no" "Private key block" "$line"
    fi

    if [[ "$line" =~ (^|[^a-zA-Z0-9_])(api[_-]?key|apikey|secret[_-]?key|access[_-]?token|auth[_-]?token)[[:space:]]*[:=][[:space:]]*[\"\']([^\"\']{8,})[\"\']([^a-zA-Z0-9_]|$) ]]; then
      report "SECRET" "$f" "$line_no" "Assigned API/secret key" "$line"
    fi

    if [[ "$line" =~ --password[[:space:]]+[\"\']([^\"\']{4,})[\"\']([^a-zA-Z0-9_]|$) ]]; then
      report "PASSWORD" "$f" "$line_no" "Hardcoded CLI password flag" "$line"
    fi

    if [[ "$line" =~ (^|[^a-zA-Z0-9_])(password|passwd|pwd)[[:space:]]*[:=][[:space:]]*[\"\']([^\"\']{4,})[\"\']([^a-zA-Z0-9_]|$) ]]; then
      report "PASSWORD" "$f" "$line_no" "Hardcoded password assignment" "$line"
    fi

    if [[ "$line" =~ \"type\"[[:space:]]*:[[:space:]]*\"api\"[[:space:]]*,[[:space:]]*\"key\"[[:space:]]*:[[:space:]]*\"[^\"]{8,}\" ]]; then
      report "SECRET" "$f" "$line_no" "auth.json-style API key entry" "$line"
    fi

    if [[ "$line" =~ \b[0-9]{3}-[0-9]{2}-[0-9]{4}\b ]]; then
      report "PII" "$f" "$line_no" "Possible SSN (###-##-####)" "$line"
    fi

    if [[ "$line" =~ \b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b ]]; then
      if [[ ! "$line" =~ @([Ee]xample\.(com|org|net)|localhost|users\.noreply\.github\.com) ]]; then
        report "PII" "$f" "$line_no" "Email address" "$line"
      fi
    fi

    if [[ "$line" =~ \b(\+1[[:space:]-]?)?(\([0-9]{3}\)|[0-9]{3})[[:space:]-]?[0-9]{3}[[:space:]-]?[0-9]{4}\b ]]; then
      report "PII" "$f" "$line_no" "Possible phone number" "$line"
    fi
  done < "$f"
}

echo "Scanning changed/untracked files in: $ROOT"
echo "Files in scope: ${#FILES[@]}"
echo ""

for f in "${FILES[@]}"; do
  should_skip_file "$f" && continue
  scan_file "$f"
done

if [ "$FINDINGS" -gt 0 ]; then
  echo "======================================================================"
  echo "  FOUND $FINDINGS potential sensitive item(s)."
  echo "  Remove or redact before committing. Do not commit secrets or PII."
  echo "======================================================================"
  exit 1
fi

echo "No sensitive content detected in changed/untracked files."
exit 0
