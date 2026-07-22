# Container testing: what we got wrong, and the correct approach

This document records a real, recurring failure mode in this repo: the test
suite stays green while the shipped container image is broken. It names the
root cause, walks the case study, and prescribes the corrective direction so
this class of regression stops reaching runtime images.

---

## 1. TL;DR

- The repo's container-behavior tests are mostly **source-grep assertions over
  Dockerfile/compose text** (`test/test-docker-user.sh`, `test/test-webhook-scripts.sh`,
  `test/test-compose-config.sh`). They prove "the recipe says X," not "the
  container does X."
- Cost: a hard regression shipped to runtime — `webhook-receiver` crashed on
  every dispatched webhook with `PermissionError` on its log bind mount — while
  every test in `scripts/validate.ps1 -Test` and the CI `test` job stayed green.
- The shipped image was **stale** relative to source (built before the fix
  commit), and nothing in the suite builds or runs the image, so drift was
  invisible. No wonder so many of these issues have been making it into the
  runtime images.
- Direction: keep pytest unit tests and keep grep tests only as fast lint; add a
  **functional image layer** that builds the image, runs the real container, and
  asserts real runtime behavior against the built artifact. This layer belongs
  in the CI `build` job (`.github/workflows/validate.yml`), not in
  `validate.ps1 -Test`, because image building is CI-only by repo convention.

---

## 2. The case study that proves the approach is broken

This is a verified regression, not a hypothetical.

**Symptom.** Every dispatched webhook crashed with:

```
PermissionError: [Errno 13] Permission denied: '/tmp/orchestrator-webhook/prompt-XXXX.md'
```

raised at `webhook_receiver/runner.py:210` (`tempfile.mkstemp(... dir=log_dir)`).
The `log_dir` is computed at `webhook_receiver/runner.py:206` as
`Path(tempfile.gettempdir()) / "orchestrator-webhook"`, i.e.
`/tmp/orchestrator-webhook`.

**Root cause.** `/tmp/orchestrator-webhook` is a host bind mount (host path
`${WEBHOOK_LOG_DIR:-./traces/runner}`, asserted present by
`test/test-compose-config.sh:40-45`). When the source host path does not exist,
the Docker daemon creates it as **root:root** on first attach. The webhook
process runs as the non-root `app` user (UID 1000) after a `gosu app` drop, so
it could not write to the root-owned directory and `mkstemp` raised `EACCES`.

**The intended fix (commit a77df2e).** Added `scripts/webhook-entrypoint.sh`,
which runs as root *before* the `gosu` drop and chowns the mount to `app`
(`scripts/webhook-entrypoint.sh:10-14`). The `Dockerfile.webhook` was changed
to install it as the real `ENTRYPOINT ["webhook-entrypoint.sh"]`
(`Dockerfile.webhook:140`) and the compose mount was paired with the change.

**What was actually running.** The deployed image
(`ghcr.io/.../webhook:development-latest`) was **stale**: it was built before
a77df2e. Its entrypoint was the bare `["gosu","app"]` form, with **no**
`webhook-entrypoint.sh` and **no** chown baked in. The mount stayed
root-owned; the dropped-to-`app` process hit `EACCES` on the first write.

**Critical point — the suite passed throughout this entire failure.** The
relevant tests assert what is *written in source*, not what is *built into or
run by* the image:

- `test/test-docker-user.sh:54` greps the Dockerfile.webhook **source text**
  for the literal `ENTRYPOINT ["webhook-entrypoint.sh"]` and reports ok. The
  running image's entrypoint was irrelevant to this assertion.
- `test/test-webhook-scripts.sh:24` greps `Dockerfile.webhook` for a `COPY`
  line for `webhook-entrypoint.sh`, and `test/test-webhook-scripts.sh:31` greps
  `.dockerignore` for a re-include line. Both assert the *recipe*, not that the
  file is actually present or executable in the published image.
- `test/test-compose-config.sh:40-45` runs `docker compose ... config` and
  checks the mount target exists *in the compose model*. The compose model is
  applied live from the working tree; it tells us nothing about the registry
  image.

All three gave false confidence while the shipped artifact was broken. That is
the failure this document exists to correct.

---

## 3. Why source-grep testing is the wrong tool

A Dockerfile is a **build recipe**, not the artifact. Grepping the recipe
cannot detect any of the failure modes that actually matter for this repo:

- A **stale published image tag** (the literal case study above — `*-latest`
  points at a build made before the fix).
- A **botched COPY** — the line is in the Dockerfile but the file was missing
  from the build context, or silently dropped by a `.dockerignore` rule, so it
  never entered the layer.
- A **multi-stage override** silently changing contents. `Dockerfile.webhook`
  documents that the `rust-builder` stage is overridden in publish builds
  (`Dockerfile.webhook:4-9`), and `.github/workflows/docker-publish.yml:188-189`
  does exactly that via `build-contexts`. A grep test cannot reason about which
  stage actually supplied the bytes.
- An **entrypoint that exists in source but was never baked into the tagged
  image** — precisely the a77df2e regression.
- **Runtime behavior**: PATH resolution, file ownership after a `chown`,
  privilege-drop ordering, mount-propagation, and uid-mapping are all runtime
  phenomena. They cannot be observed by reading text.

A grep test encodes **"the Dockerfile says X"** rather than **"the container
does X."** It is structurally immune to the exact failure mode that bites us:
**image/compose deploy drift** — compose applied live from a working tree while
the registry image lags behind because the fix commit sat on a non-publish
branch. Note `docker-publish.yml:3-5` only triggers on push to
`main`/`development`/`nam20485`; a correct fix merged anywhere that does not
trigger publish leaves the `*-latest` tag pointing at the old, broken image.
The grep suite has no way to see this gap.

---

## 4. The correct approach: test the artifact, run the container

A layered strategy, tailored to this repo.

### 4.1 Unit tests — keep, in their current role

`pytest` under `tests/` (run via `uv run pytest`, invoked from
`scripts/validate.ps1:113-119`) covers pure Python logic — webhook filters,
runner helpers, settings parsing. These are real tests. **The critique in this
document applies only to the container/image grep tests, not to pytest.**
Unit tests stay the primary local signal.

### 4.2 Static guards — relabel honestly

The existing grep scripts (`test/test-docker-user.sh`,
`test/test-webhook-scripts.sh`, `test/test-compose-config.sh`) are acceptable
**only** as fast, early-fail lint over source — a cheap way to catch a typo or
a dropped `COPY` line before the slow `docker build` runs. They must never be
the **sole** proof of container behavior. Today they are presented as
contract tests ("Verifies the configuration the non-root runtime relies on",
`test/test-docker-user.sh:7-12`); that framing is the problem, because it
implies they verify runtime behavior. Relabel their role honestly: they lint
the recipe, nothing more.

### 4.3 Functional image tests — the missing layer

**Build the image, then run the real container and assert real behavior
against the built artifact.** This is the layer that would have caught the
case study.

The canonical prescribed artifact is `test/test-webhook-image-entrypoint.sh`,
which performs four behaviors, each of which matters:

1. **Reproduce the root-owned bind mount.** Mount a **non-existent** host path
   into the container at `/tmp/orchestrator-webhook` so Docker itself creates
   it as `root:root` on first attach — exactly the production trigger. Using a
   pre-created, pre-owned dir would hide the bug.
2. **Run the real image with its real entrypoint.** No `--entrypoint` override.
   The point is to exercise the entrypoint the shipped image actually carries.
3. **Prove the dropped-to-`app` process (UID 1000) can write under
   `/tmp/orchestrator-webhook`.** This is the literal operation that failed at
   `webhook_receiver/runner.py:210`. Asserting it from inside the container,
   as UID 1000, after the `gosu` drop, is the only direct proof.
4. **Assert the `chown` propagated to the host dir** (host-side uid is 1000
   after the run). This proves the entrypoint's fixup (`scripts/webhook-entrypoint.sh:12-14`)
   executed as root before the drop and mutated the real bind mount.

**Why each step matters and why it would have caught the regression:** a stale
image whose entrypoint is the bare `["gosu","app"]` (no `webhook-entrypoint.sh`,
no chown) leaves the mount root-owned, so step 3 fails with `EACCES` and step 4
fails because the host uid stays 0. The grep suite cannot observe either.

### 4.4 Where functional image tests belong: the CI `build` job

Functional image tests require a built image. By repo convention, **local
validation does not build images** — `scripts/validate.ps1` runs lint, scan,
pytest, Pester, and the bash integration scripts, but never `docker build`
(see `scripts/validate.ps1:112-139`). Image building is the separate
`.github/workflows/validate.yml` `build` job, which already builds
`orchestrator-webhook:ci` with `load: true`
(`.github/workflows/validate.yml:106-115`).

Therefore functional image tests belong as **steps in the `build` job, after
the images are built**, not in `validate.ps1 -Test`. This preserves the
local/CI parity rule: `validate.ps1` mirrors the `test` job; the `build` job
is CI-only for **both** image building **and** image-functional tests. The
parity rule is not weakened — it is respected by keeping image-functional
tests out of the local script entirely.

### 4.5 Negative-control discipline

Every functional test must be proven to **fail** against a known-broken
artifact before it is trusted. Concretely: build a variant image from a
Dockerfile with the pre-fix `ENTRYPOINT ["gosu","app"]` (no
`webhook-entrypoint.sh`, no chown), run the functional test against it, and
confirm it exits non-zero. **A test that cannot be made to fail is not a
test.** This should be a documented manual check (or a periodic CI step) for
each functional test.

---

## 5. Concrete migration recommendations (prioritized)

1. **Audit `test/test-*.sh`; classify each as static-guard vs functional.**
   Move or rewrite every script that claims to verify runtime behavior —
   non-root execution, privilege drop, entrypoint behavior, mounts, file
   ownership — into functional image tests that run in the `build` job. The
   grep versions can stay only if relabeled as pure lint.
2. **Add at least one functional smoke per runtime image** in the `build` job,
   for each of `orchestratorservice`, `orchestrator-webhook`,
   `orchestrator-caddy`: run the real container, assert the non-root user, and
   assert the entrypoint executed its fixups (memory-dir chown for the main
   image per `scripts/docker-entrypoint.sh`; `/data`+`/config` chown for caddy
   per `deploy/caddy/caddy-entrypoint.sh`; `/tmp/orchestrator-webhook` chown
   for webhook per `scripts/webhook-entrypoint.sh`).
3. **Keep pytest unit tests as the primary local signal; keep static-grep
   scripts only as fast lint.** Do not let green local `validate.ps1 -Test`
   be read as evidence the image works — it never builds the image.
4. **Add a negative-control CI step (or documented manual check)** that builds
   a deliberately-broken variant per section 4.5 and confirms the functional
   test fails. Gate trust in each functional test on this control.

---

## 6. Anti-patterns to avoid

- **"Grep the Dockerfile and reason about how it will work."** Reasoning about
  runtime behavior from build-recipe text is exactly what produced the case
  study. Stop doing it as a verification step.
- **Asserting `docker compose config` output as proof of runtime behavior.**
  `config` validates the model against the working tree; it says nothing about
  the registry image actually being run.
- **Treating a green local `validate.ps1 -Test` as evidence the image works.**
  It never builds or runs the image. It can only ever prove the source text is
  internally consistent.
- **Adding a runtime workaround in app code to paper over a broken image
  contract** — e.g. fallback directories or `try/except` around `mkstemp` in
  `webhook_receiver/runner.py`. That hides the failure and lets the broken
  artifact keep shipping. Fix the artifact (entrypoint/image), test the
  artifact, and let the app code keep its honest, narrow contract.
