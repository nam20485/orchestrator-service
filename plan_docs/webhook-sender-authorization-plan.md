# Webhook Sender Authorization: Login Allowlist for `issues.labeled` Dispatch

**Status:** Plan / POR (implementation pending separate approval)
**Scope:** Application-level authorization of the GitHub actor on the
`webhook_receiver` dispatch path (`webhook_receiver/filters.py::should_dispatch`)
**Authoritative sources:** `webhook_receiver/filters.py`, `webhook_receiver/app.py`,
`webhook_receiver/config.py`, `webhook_receiver/github.py`, `tests/test_filters.py`
**Companion doc:** [`webhook-auth-security-options.md`](./webhook-auth-security-options.md)
— covers **delivery authenticity** (HMAC, replay, IP allowlist). This doc adds the
**authorization** dimension that the companion doc explicitly did not cover.

---

## 1. Problem Statement

The orchestrator webhook receiver authenticates **delivery authenticity** but performs
**no application-level authorization** on the actor for the general dispatch path.
Today, only the cryptographic guarantee that "GitHub sent this payload" is enforced;
the question of **whether the actor described in that payload is allowed to trigger
orchestration** is answered only implicitly by GitHub's repo-permission model, with
no explicit, auditable, application-level backstop.

### Authenticity vs. authorization (the key distinction)

These are two separate security controls. Neither substitutes for the other.

| Concern | Control | What it proves |
|---|---|---|
| **Authenticity (integrity)** | Verify `X-Hub-Signature-256` HMAC-SHA256 with `OS_WEBHOOK_SECRET` | "This exact payload was sent by GitHub and was not tampered with in transit." |
| **Authorization** | Inspect the actor inside the **verified** payload (`sender.login`, `author_association`, team membership) | "The user/app who performed the action is permitted to trigger this bot behavior." |

**Signature verification ONLY proves "GitHub sent this." It does NOT prove "an
authorized user performed the action."** A malicious user triggering an event on a
public repo produces a *genuinely-signed* payload describing *that user* as the actor.
The HMAC check passes; only an authorization check decides whether to act. Per GitHub's
[validating-webhook-deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
guidance, signature verification ensures deliveries "were sent by GitHub" and "not
tampered with" — it makes no claim about the actor.

### The incident this traces back to

The receiver was observed dispatching orchestration in response to an outside user
replying to an issue. That specific *comment→orchestration* path is **already closed**
on the Python side (see §2). What remains open is a *latent* gap on the
`issues.labeled` dispatch path: it relies entirely on GitHub's implicit label-permission
(triage+) for authorization, with nothing in the application to catch a regression or a
broadly-granted permission. This plan closes that gap with an explicit allowlist.

---

## 2. Findings (verified against the current code)

All references are to the tree at the time of writing.

### 2.1 Authenticity layer — present and correct

`verify_signature` (`webhook_receiver/github.py:13-28`) performs constant-time
HMAC-SHA256 verification of `X-Hub-Signature-256`, called before any dispatch in
`app.py:259-265` (HTTP 401 on failure). `OS_WEBHOOK_SECRET` is **required** at startup
(`config.py:111-115`). The legacy sha1 `X-Hub-Signature` is not verified (sha256 only).

### 2.2 Authorization layer — absent for the general path

`should_dispatch` (`filters.py:97-138`) is the single transport-level gate. It checks,
in order:

1. `event ∈ {"issues"}` (`filters.py:107-108`) — `issue_comment`, `pull_request`,
   `pull_request_review`, `push`, `workflow_run`, etc. are **all rejected** here. This
   is why the comment→orchestration hole is closed.
2. `action ∈ {"labeled"}` (`filters.py:109-110`) — `opened`, `closed`, `edited`, etc.
   rejected.
3. Sender is not a bot (`filters.py:113-114`, `_is_bot_actor`) — anti-echo-loop only,
   **not** authorization; any non-bot human or PAT user passes.
4. Label is workflow-relevant (`filters.py:116-118`, `_is_workflow_label`) — routing,
   not authorization.
5. **If and only if** the label is `gh-issue-tracking:direct-body`: sender must be in
   `DIRECT_BODY_ALLOWED_SENDERS`, fail-closed (`filters.py:125-137`).

**For every other dispatch label** (`orchestration:*`, other `gh-issue-tracking:*`,
`implementation:ready/complete`) there is **zero** sender restriction. A repo-wide grep
confirms **no** `author_association` / `OWNER` / `MEMBER` / `COLLABORATOR` check exists
anywhere in Python (the only occurrences are in `.github/workflows/droid.yml` and in
captured-payload JSON logs, not in logic). A payload with **no `sender` field at all**
is explicitly allowed — `tests/test_filters.py:208-212` asserts `allow is True` for a
missing sender.

### 2.3 The one existing sender gate

`DIRECT_BODY_ALLOWED_SENDERS` (`filters.py:76-83` define; `:125-137` check) is a
comma-separated, case-insensitive, live-read-from-env set of GitHub login strings. It is
**fail-closed** when unset. It gates exactly one label because that label runs the issue
body verbatim as the orchestrator prompt with the orchestration token + `--auto` (a
confused-deputy risk — documented at `filters.py:64-73`). It is a sound, proven pattern
and is the template this plan generalizes.

### 2.4 Comparison with `droid.yml`

`.github/workflows/droid.yml:18-22` gates the `droid` GHA job on
`author_association ∈ {OWNER, MEMBER, COLLABORATOR}` for `issue_comment`,
`pull_request_review_comment`, `pull_request_review`, `issues`, and `pull_request`
events. That pattern works there because on those events the **actor IS the author** of
the comment/review/issue, so the association is inline in the payload. The
`webhook_receiver` is materially weaker on sender authorization: it has no association
check at all and its only allowlist is scoped to one label.

---

## 3. Why `author_association` Alone Does Not Fit the `issues.labeled` Path

The `droid.yml` technique (`author_association ∈ {OWNER,MEMBER,COLLABORATOR}`) cannot
be applied directly to the receiver's dispatch path, because of the payload shape:

- The receiver dispatches on **`issues.labeled`**, where the actor is the **labeler**
  (`sender`), **not** the issue author.
- In a labeled payload, the **`sender` object carries only `login`** — it has **no
  `author_association` field**. Verified against the fixture
  (`test/fixtures/github/issues-labeled.json:13`: `"sender": { "login": "test-user" }`)
  and against a real captured payload (`traces/runner/prompt-3lpf7r3c.md:328`, where
  `author_association: OWNER` lives **inside** the `issue` object).
- `issue.author_association` describes the **issue author**, a different person who may
  not be the one applying the label. Reading it would authorize the **wrong actor** and
  is bypassable whenever author ≠ labeler.

GitHub also has a documented reliability caveat: a member with their "Organization
visibility" set to **private** appears as `NONE`, not `MEMBER`
([community discussion #18690](https://github.com/orgs/community/discussions/18690)).
So `author_association` is a reliable *deny-list* but a slightly *leaky allow-list*.

**Decision:** Use an explicit login allowlist instead (generalizing
`DIRECT_BODY_ALLOWED_SENDERS`). It is semantically correct (checks the actual labeler),
predictable, and immune to the org-visibility quirk. The cost is manual maintenance of
the login list. A possible future Phase 2 (API-based `author_association` lookup) is
deferred.

---

## 4. Chosen Design (locked decisions)

1. **Mechanism — explicit login allowlist.** `sender.login` must be in a set of trusted
   logins, read live from the environment on each call, case-insensitive. Identical in
   shape to `_direct_body_allowed_senders()` (`filters.py:76-83`).
2. **Configurable — via new env var `WEBHOOK_ALLOWED_SENDERS`** (comma-separated GitHub
   logins). No code change needed to tighten or loosen per deployment.
3. **Default — fail-closed.** When `WEBHOOK_ALLOWED_SENDERS` is unset/empty, ALL
   `issues.labeled` dispatch is rejected. Consistent with `DIRECT_BODY_ALLOWED_SENDERS`.

### Layering with `DIRECT_BODY_ALLOWED_SENDERS`

The global gate runs **first**, for **all** labels. The existing direct-body gate
(`filters.py:125-137`) stays as a **narrower second gate** on top — it is not removed or
deprecated. Direct-body therefore requires membership in **both** lists. All existing
direct-body behavior and tests are preserved.

### Critical operational constraint: the self-driving pipeline

The orchestration pipeline is **self-driving**: the orchestrator agent applies the next
label in the epic sequence (`epic-ready` → `epic-implemented` → `epic-reviewed` →
`epic-complete`) via the PAT user. `test_should_dispatch_allows_pat_user`
(`tests/test_filters.py:100-104`) confirms the PAT login is a **non-bot** whose
`issues.labeled` events dispatch today.

**The PAT / self-driving login MUST be in `WEBHOOK_ALLOWED_SENDERS` or the entire
pipeline stalls after the first label.** This is the dominant risk of a fail-closed
default. Mitigations: (a) a startup WARNING naming the var and the self-drive
implication; (b) the env-var table + comment; (c) this doc.

---

## 5. Requirements & Acceptance Criteria

Implementation is approved separately. When approved, the work must satisfy:

- [ ] `webhook_receiver/filters.py`: add `_allowed_senders()` reader (clone of
      `_direct_body_allowed_senders`, reading `WEBHOOK_ALLOWED_SENDERS`).
- [ ] In `should_dispatch`, after the `_is_bot_actor` check and before the label-specific
      logic: empty allowlist ⇒ `(False, "dispatch disabled (set
      WEBHOOK_ALLOWED_SENDERS to enable)")`; non-empty and `sender` not in set ⇒
      `(False, f"dispatch not permitted for sender {sender!r}")`.
- [ ] `_is_bot_actor` check remains first (cheap deny); the direct-body gate
      (`filters.py:125-137`) remains unchanged as the narrower second gate.
- [ ] A missing `sender` is rejected when the allowlist is non-empty (fail-closed) —
      update `tests/test_filters.py:208-212` to the new, stricter, correct behavior.
- [ ] Startup WARNING in `webhook_receiver/app.py` (or `config.py`) when
      `WEBHOOK_ALLOWED_SENDERS` is unset/empty, naming the var and the self-drive
      implication.
- [ ] `compose.yaml`: add `- WEBHOOK_ALLOWED_SENDERS=${WEBHOOK_ALLOWED_SENDERS}` next to
      `DIRECT_BODY_ALLOWED_SENDERS` (line ~72) with a fail-closed + self-drive comment.
- [ ] `docs/environment-variables.md`: add a `WEBHOOK_ALLOWED_SENDERS` row.
- [ ] `tests/test_filters.py`: autouse fixture defaulting the allowlist to the test
      sender so existing tests pass; new tests mirror the direct-body suite
      (rejected-by-default, rejected-for-unlisted, allowed-for-listed,
      case-insensitive, direct-body-requires-both-lists).
- [ ] Test coverage stays **> 85%**.

---

## 6. Validation Plan

1. `pwsh -NoProfile -File ./scripts/validate.ps1 -All` (lint + scan + test) — must be green.
2. Python: `uv run pytest tests/ -q` — all existing + new tests pass.
3. Manual: confirm the startup WARNING fires when the var is unset.
4. Manual: confirm a labeled event from an unlisted sender is `202 ignored` with the new
   reason.
5. Commit on a new branch (e.g. `mn/webhook-sender-allowlist`) off `development`; PR
   titled "feat(webhook): fail-closed sender allowlist for issues.labeled dispatch".

---

## 7. Phased Development

- **Phase 1 (this plan):** Global login allowlist gate + startup WARNING + compose/env
  docs + tests. Login-list based; predictable and immune to the org-visibility quirk.
- **Phase 2 (deferred):** Optional GitHub API `author_association` lookup
  (`GET /repos/{owner}/{repo}/collaborators/{sender}/permission` via the existing
  `GH_ORCHESTRATION_AGENT_TOKEN`) if login-list maintenance becomes burdensome. Trades
  one API call per dispatch + token dependency for self-maintaining authorization.

---

## 8. Out of Scope

- HMAC verification changes (sha256 constant-time verify stays as-is).
- Changes to the `should_dispatch` event/action allow-sets.
- Changes to the orchestration prompt template (`orchestration_prompt.jinja2.md`).
- Deprecating `DIRECT_BODY_ALLOWED_SENDERS` — it remains the narrower second gate.
- Hardening GitHub App installation itself (owned by GitHub; see companion doc §3.3).
- Threading `sender` into the dispatch / prompt arguments (the agent sees the raw event
  JSON already; this plan authorizes at the transport gate, before dispatch).

---

## 9. Sources

- GitHub — [Validating webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries) (HMAC, constant-time, what signature verification proves)
- GitHub — [Handling webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/handling-webhook-deliveries) (validate before processing)
- GitHub — [Webhook events and payloads](https://docs.github.com/en/webhooks/webhook-events-and-payloads) (`sender`, `issue_comment`, `pull_request_review`)
- GitHub REST API — [Issue comments](https://docs.github.com/en/rest/issues/comments) (`author_association` enum: OWNER, MEMBER, COLLABORATOR, CONTRIBUTOR, FIRST_TIME_CONTRIBUTOR, FIRST_TIMER, MANNEQUIN, NONE)
- GitHub — [Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions) (least privilege, untrusted-actor risk)
- GitHub Community — [Discussion #18690](https://github.com/orgs/community/discussions/18690) (private org-membership visibility → `NONE`)
- GitHub Community — [Discussion #78038](https://github.com/orgs/community/discussions/78038) (`author_association` values + reliability)
- GitHub — [Limiting interactions in your repository](https://docs.github.com/en/communities/moderating-comments-and-conversations/limiting-interactions-in-your-repository) (complementary repo-side control)
