#!/usr/bin/env bash
# Scan staged, unstaged, and untracked (non-ignored) files for sensitive content.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

if [ -n "${SECRET_SCAN_FILES:-}" ]; then
  # Test hook: scan explicit paths (space-separated), skip git discovery.
  read -ra FILES <<< "$SECRET_SCAN_FILES"
else
  mapfile -t FILES < <(
    {
      git diff --name-only --diff-filter=ACMRTUXB HEAD 2>/dev/null || true
      git diff --cached --name-only --diff-filter=ACMRTUXB 2>/dev/null || true
      git ls-files --others --exclude-standard 2>/dev/null || true
    } | sort -u
  )
fi

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

is_lockfile() {
  local f="$1"
  local base="${f##*/}"
  [[ "$base" == *.lock ]] && return 0
  return 1
}

is_blocked_filename() {
  local f="$1"
  local base="${f##*/}"
  [[ "$base" == .env* ]] && return 0
  [[ "$base" == .envrc ]] && return 0
  [[ "$base" == auth.json ]] && return 0
  [[ "$base" == secrets.toml ]] && return 0
  [[ "$base" == .pypirc ]] && return 0
  [[ "$base" == credentials.json ]] && return 0
  [[ "$base" == id_rsa ]] && return 0
  [[ "$base" == id_dsa ]] && return 0
  [[ "$base" == id_ecdsa ]] && return 0
  [[ "$base" == id_ed25519 ]] && return 0
  [[ "$base" == *.pem ]] && return 0
  [[ "$base" == *.p12 ]] && return 0
  return 1
}

should_skip_file() {
  local f="$1"
  [[ "$f" == *"/scan-uncommitted-secrets/"* ]] && return 0
  # Intentional fixtures; exercised via test/test-scan-secrets.sh (SECRET_SCAN_FILES).
  if [ -z "${SECRET_SCAN_FILES:-}" ] && [[ "$f" == test/fixtures/secret-scan/* ]]; then
    return 0
  fi
  if [ -z "${SECRET_SCAN_FILES:-}" ]; then
    git check-ignore -q "$f" 2>/dev/null && return 0
  fi
  [[ ! -f "$f" ]] && return 0
  is_binary "$f" && return 0
  return 1
}

is_allowlisted_line() {
  local f="$1"
  local line="$2"
  [[ "$line" =~ FAKE-.+-FOR-TESTING ]] && return 0
  [[ "$line" =~ example\.(com|org|net) ]] && return 0
  [[ "$line" =~ @example\. ]] && return 0
  [[ "$line" =~ your-.*-key ]] && return 0
  [[ "$line" =~ \<YOUR_.*\> ]] && return 0
  [[ "$line" =~ changeme|placeholder|redacted|REDACTED ]] && return 0
  [[ "$line" =~ =[[:space:]]*[\"\']…[\"\'] ]] && return 0
  # Compose-style interpolation: ${VAR}, ${VAR:-default}, ${VAR:?msg}
  [[ "$line" =~ \$\{[A-Za-z_][A-Za-z0-9_]*(:[?\-][^}]*)?\} ]] && return 0
  # Lockfile wheel/package digest pins (exact 64-hex SHA-256) are not secrets.
  # Scoped to lockfiles so a real secret sharing a digest-like line elsewhere is still caught.
  is_lockfile "$f" && [[ "$line" =~ sha256:[a-f0-9]{64} ]] && return 0
  return 1
}

is_allowlisted_secret_value() {
  local val="$1"
  [[ "$val" =~ ^\$\{[A-Za-z_][A-Za-z0-9_]*\}$ ]] && return 0
  [[ "$val" == "…" ]] && return 0
  [[ "$val" == "..." ]] && return 0
  [[ "$val" =~ FAKE-.+-FOR-TESTING ]] && return 0
  [[ "$val" == "secret" ]] && return 0
  [[ "$val" == "changeme" ]] && return 0
  [[ "$val" == "placeholder" ]] && return 0
  [[ "$val" =~ ^paste- ]] && return 0
  [[ ${#val} -lt 8 ]] && return 0
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

scan_line() {
  local f="$1"
  local line_no="$2"
  local line="$3"
  local skip_pii="$4"

  is_allowlisted_line "$f" "$line" && return 0

  if [[ "$line" =~ (AKIA[0-9A-Z]{16}) ]]; then
    report "API_KEY" "$f" "$line_no" "AWS access key id" "$line"
  fi

  if [[ "$line" =~ (ASIA[0-9A-Z]{16}) ]]; then
    report "API_KEY" "$f" "$line_no" "AWS temporary access key id" "$line"
  fi

  if [[ "$line" =~ (sk-or-v1-[a-zA-Z0-9_-]{10,}) ]]; then
    report "API_KEY" "$f" "$line_no" "OpenRouter/OpenAI-style key" "$line"
  fi

  if [[ "$line" =~ (sk-proj-[a-zA-Z0-9_-]{10,}) ]]; then
    report "API_KEY" "$f" "$line_no" "OpenAI project key" "$line"
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

  if [[ "$line" =~ (gho_[a-zA-Z0-9]{20,}) ]]; then
    report "TOKEN" "$f" "$line_no" "GitHub OAuth token" "$line"
  fi

  if [[ "$line" =~ (ghu_[a-zA-Z0-9]{20,}) ]]; then
    report "TOKEN" "$f" "$line_no" "GitHub user token" "$line"
  fi

  if [[ "$line" =~ (ghr_[a-zA-Z0-9]{20,}) ]]; then
    report "TOKEN" "$f" "$line_no" "GitHub refresh token" "$line"
  fi

  if [[ "$line" =~ (github_pat_[a-zA-Z0-9_]{20,}) ]]; then
    report "TOKEN" "$f" "$line_no" "GitHub fine-grained PAT" "$line"
  fi

  if [[ "$line" =~ (glpat-[a-zA-Z0-9_-]{10,}) ]]; then
    report "TOKEN" "$f" "$line_no" "GitLab personal access token" "$line"
  fi

  if [[ "$line" =~ (xox[baprs]-[a-zA-Z0-9-]{10,}) ]]; then
    report "TOKEN" "$f" "$line_no" "Slack token" "$line"
  fi

  if [[ "$line" =~ (Bearer[[:space:]]+[a-zA-Z0-9\-._~+/]{20,}) ]]; then
    report "TOKEN" "$f" "$line_no" "Bearer token" "$line"
  fi

  if [[ "$line" =~ eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,} ]]; then
    report "TOKEN" "$f" "$line_no" "Possible JWT" "$line"
  fi

  if [[ "$line" =~ -----BEGIN[[:space:]](RSA[[:space:]]|OPENSSH[[:space:]]|EC[[:space:]]|)PRIVATE[[:space:]]KEY----- ]]; then
    report "SECRET" "$f" "$line_no" "Private key block" "$line"
  fi

  if [[ "$line" =~ (^|[^a-zA-Z0-9_])(api[_-]?key|apikey|secret[_-]?key|access[_-]?token|auth[_-]?token)[[:space:]]*[:=][[:space:]]*[\"\']([^\"\']{8,})[\"\']([^a-zA-Z0-9_]|$) ]]; then
    report "SECRET" "$f" "$line_no" "Assigned API/secret key" "$line"
  fi

  if [[ "$line" =~ (OS_WEBHOOK_SECRET|OPENCODE_SERVER_PASSWORD|ZAI_CODING_API_KEY|ZAI_API_KEY|OPENROUTER_API_KEY|MODEL_STUDIO_API_KEY|GH_ORCHESTRATION_AGENT_TOKEN|GITHUB_TOKEN)[[:space:]]*=[[:space:]]*\"([^\"]{8,})\" ]]; then
    local assigned_val="${BASH_REMATCH[2]}"
    if ! is_allowlisted_secret_value "$assigned_val"; then
      report "SECRET" "$f" "$line_no" "Hardcoded repo credential env var" "$line"
    fi
  fi

  if [[ "$line" =~ (OS_WEBHOOK_SECRET|OPENCODE_SERVER_PASSWORD|ZAI_CODING_API_KEY|ZAI_API_KEY|OPENROUTER_API_KEY|MODEL_STUDIO_API_KEY|GH_ORCHESTRATION_AGENT_TOKEN|GITHUB_TOKEN)[[:space:]]*=[[:space:]]*\'([^\']{8,})\' ]]; then
    local assigned_val="${BASH_REMATCH[2]}"
    if ! is_allowlisted_secret_value "$assigned_val"; then
      report "SECRET" "$f" "$line_no" "Hardcoded repo credential env var" "$line"
    fi
  fi

  if [[ "$line" =~ (OS_WEBHOOK_SECRET|OPENCODE_SERVER_PASSWORD|ZAI_CODING_API_KEY|ZAI_API_KEY|OPENROUTER_API_KEY|MODEL_STUDIO_API_KEY|GH_ORCHESTRATION_AGENT_TOKEN|GITHUB_TOKEN)[[:space:]]*=[[:space:]]*([^[:space:]#]+) ]]; then
    local bare_val="${BASH_REMATCH[2]}"
    if [[ "$bare_val" =~ ^[\"\'] ]]; then
      :
    elif [[ "$bare_val" =~ ^\$\{[A-Z0-9_]+\}$ ]] || is_allowlisted_secret_value "$bare_val"; then
      :
    elif [ ${#bare_val} -ge 8 ]; then
      report "SECRET" "$f" "$line_no" "Hardcoded repo credential env var (unquoted)" "$line"
    fi
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

  if [ "$skip_pii" -eq 1 ]; then
    return 0
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
}

scan_file() {
  local f="$1"
  local skip_pii=0
  is_lockfile "$f" && skip_pii=1

  local line_no=0
  local line

  while IFS= read -r line || [ -n "$line" ]; do
    line_no=$((line_no + 1))
    scan_line "$f" "$line_no" "$line" "$skip_pii"
  done < "$f"
}

echo "Scanning changed/untracked files in: $ROOT"
echo "Files in scope: ${#FILES[@]}"
echo ""

for f in "${FILES[@]}"; do
  if is_blocked_filename "$f"; then
    report "BLOCKED_FILE" "$f" "0" "Credential filename (must not be committed)" "$f"
    continue
  fi
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
