# Plan: Add `--build` flag to dc.ps1 for local image builds

## Context

`compose.build.yaml` already exists as a local-build overlay that:
- Adds `build:` directives for all 3 services (orchestratorservice, webhook-receiver, webhook-proxy)
- Sets `pull_policy: never` to prevent re-pulling from GHCR

Currently, using `--build` via ExtraArgs doesn't work well because:
1. `--pull always` is unconditionally added for `up` (line 88), conflicting with local builds
2. The overlay file isn't automatically layered, so `build:` directives aren't active

## Changes

**File: `scripts/dc.ps1`**

1. After the `up` block setup (around line 76-89), detect if `--build` is present in `$ExtraArgs`
2. If `--build` is detected:
   - Add `-f $repoRoot/compose.build.yaml` to `$composeArgs` (insert after the base `-f` pair, before the command)
   - Skip adding `--pull always`
3. If `--build` is NOT detected, keep current behavior (`--pull always`)

### Implementation detail

Insert the `--build` detection before the existing `--pull always` logic:

```powershell
# Detect --build in ExtraArgs to switch to local-build mode
$hasBuild = $false
if ($ExtraArgs) {
    foreach ($a in $ExtraArgs) { if ($a -eq '--build') { $hasBuild = $true; break } }
}

if ($Command -eq 'up') {
    # ... existing foreground detection ...
    if (-not $hasForeground) { $composeArgs += '-d' }
    if ($hasBuild) {
        # Layer the local-build overlay; skip --pull always
        $composeArgs = @('compose', '-f', $composeFile, '-f', (Join-Path $repoRoot 'compose.build.yaml'), $Command)
        if (-not $hasForeground) { $composeArgs += '-d' }
    } else {
        $composeArgs += '--pull', 'always'
    }
}
```

Note: `$composeArgs` is initialized on line 72 as `@('compose', '-f', $composeFile, $Command)`. When `--build` is detected, we need to rebuild the array to insert the second `-f` before the command. The cleanest approach is to restructure the array construction slightly, or splice the overlay `-f` into the existing array.

**Simpler approach** — just insert the overlay `-f` into the existing array before appending other args:

```powershell
$hasBuild = $false
if ($ExtraArgs) {
    foreach ($a in $ExtraArgs) { if ($a -eq '--build') { $hasBuild = $true; break } }
}
```

Then in the `up` block, replace:
```powershell
$composeArgs += '--pull', 'always'
```
with:
```powershell
if (-not $hasBuild) {
    $composeArgs += '--pull', 'always'
}
```

And add the overlay `-f` right after `$composeArgs` is initialized (line 72), before the `up` block:
```powershell
if ($hasBuild) {
    $composeArgs = @('compose', '-f', $composeFile, '-f', (Join-Path $repoRoot 'compose.build.yaml'), $Command)
}
```

## Usage after change

```powershell
./scripts/dc.ps1 u nam20485 --build
```

This will:
- Layer `compose.build.yaml` over `compose.yaml`
- Build all 3 images from local Dockerfiles
- Skip `--pull always`
- Start the stack detached

## Validation

1. Run `./scripts/dc.ps1 u nam20485 --build` — verify images are built locally and stack starts
2. Run `./scripts/dc.ps1 u nam20485` — verify existing pull-from-GHCR behavior is unchanged
3. Run `./scripts/dc.ps1 d nam20485` — verify down still works
