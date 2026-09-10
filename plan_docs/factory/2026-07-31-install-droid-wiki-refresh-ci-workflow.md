## Install the Droid Wiki Refresh workflow

### Findings (already verified)
- Inside a Git repository.
- CI framework: **GitHub Actions** (`.github/workflows/` present, no `.gitlab-ci.yml`).
- No existing wiki refresh action: no workflow matches both `droid` and `wiki`.
- Default branch: **`development`** (from `refs/remotes/origin/HEAD`).

### Step 1 — Create `.github/workflows/droid-wiki-refresh.yml`

```yaml
name: Droid Wiki Refresh

on:
  push:
    branches: [development]

jobs:
  wiki-refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2

      - name: Install Factory Droid
        run: curl -fsSL https://app.factory.ai/cli | sh

      - name: Generate wiki
        run: droid exec --auto high "/wiki"
        env:
          FACTORY_API_KEY: ${{ secrets.FACTORY_API_KEY }}
```

**One intentional deviation from the skill template:** `actions/checkout` is pinned by full SHA with a version comment instead of `@v4`, because `AGENTS.md` makes SHA pinning mandatory for every `uses:` line and `actionlint` runs over all workflows in CI. The SHA is the one already used in `.github/workflows/validate.yml`.

### Step 2 — Validate
Run `actionlint` (via `pwsh -NoProfile -File ./scripts/validate.ps1 -Lint`, the repo's CI-mirroring gate) so the new workflow does not break the `lint` job, and fix anything it reports.

### Step 3 — Report the secret requirement
Remind you to add `FACTORY_API_KEY` as a repository secret in GitHub Actions settings, since the workflow cannot authenticate without it.

### Step 4 — Offer a pull request (only with your confirmation)
I will ask before any Git mutation. If you approve, I will create branch `factory/install-wiki-ci`, stage **only** the new workflow file, commit as `ci: add Droid Wiki refresh action`, push, and open a PR against `development`.

Your untracked `droid-wiki/`, `.factory/`, and other local changes will be left untouched and unstaged.