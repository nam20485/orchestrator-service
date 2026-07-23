# Kilo Code VS Code Extension — ripgrep "Could not find @vscode/ripgrep-linux-x64" Error

> Status: **Diagnosed — root cause confirmed; solution verified (not yet applied).**
> Date: 2026-07-22
> Editor: VS Code Insiders
> Extension: `kilocode.kilo-code` 7.4.15 (also 7.4.11 installed)
> Platform: linux-x64

## Symptom

The Kilo Code extension repeatedly prints:

```
Could not find @vscode/ripgrep-linux-x64. Ensure optionalDependencies are installed for this platform (linux-x64).
```

whenever a search/grep code tool is invoked.

## Root cause

The error is thrown by **`bin/kilo`** — the Bun-compiled single-executable CLI binary bundled
inside the Kilo Code extension — *not* by VS Code itself, and *not* by the extension's
`dist/extension.js`.

### The throwing code (deobfuscated from the compiled binary)

`bin/kilo` embeds the **Morph SDK** (`@morphllm/morphsdk` v0.2.166), which declares
`@vscode/ripgrep ^1.17.0` as a dependency. The SDK's ripgrep path resolver
(`tools/warp_grep/utils/ripgrep.ts`) runs this at module-init time:

```js
import { createRequire } from "module";

const req = createRequire(import.meta.url);
const arch = process.env.npm_config_arch || "x64";            // -> "x64"
const pkg  = `@vscode/ripgrep-linux-${arch}`;                  // -> @vscode/ripgrep-linux-x64
let rgPath;
try {
  rgPath = req.resolve(`${pkg}/bin/rg`);                       // looks for the platform binary
} catch {
  throw Error(`Could not find ${pkg}. Ensure optionalDependencies are installed for this platform (linux-${arch}).`);
}
export { rgPath };
```

### Why it fails on this install

1. **The VSIX ships no `rg` package.** The extension folder
   `~/.vscode-insiders/extensions/kilocode.kilo-code-7.4.15-linux-x64/` contains **no
   `node_modules/` at all** and **no bundled `rg` binary**. Bun's `--compile` embedded the
   resolver's *JavaScript* but did **not** embed the platform-specific
   `@vscode/ripgrep-linux-x64` package whose `bin/rg` native binary lives outside the bundle.
2. `createRequire(import.meta.url).resolve('@vscode/ripgrep-linux-x64/bin/rg')` walks
   `node_modules` upward from `bin/kilo`'s directory and finds nothing → the `catch` block
   **unconditionally re-throws** the exact message above.
3. **The system-`rg` fallback is dead code.** The spawner (`runRipgrep` below) *does* intend
   to fall back to a `rg` on `PATH` — but that fallback lives in a function reached only
   *after* the resolver module initializes. Because the resolver `throw`s during init, the
   fallback is never reached. **This is why putting `rg` on `PATH` alone does not fix it.**

   ```js
   async function runRipgrep(args, opts) {
     if (resolved && cachedRg) return spawn(cachedRg, args, opts);
     if (!resolved) {
       const bundled = await spawn(rgPath, args, opts);          // rgPath from resolver
       if (!isUnusable(bundled)) { cachedRg = rgPath; resolved = true; return bundled; }
       const system = await spawn("rg", args, opts);             // <-- intended fallback
       if (!isUnusable(system))  { cachedRg = "rg";  resolved = true; return system; }
       return { stdout: "", stderr: "Failed to spawn ripgrep. Neither bundled nor system rg is available.", exitCode: -1 };
     }
     return { stdout: "", stderr: "Failed to spawn ripgrep. Neither bundled nor system rg is available.", exitCode: -1 };
   }
   ```

4. `npm_config_arch` is unset → defaults to `"x64"`, so the missing package name is
   `@vscode/ripgrep-linux-x64`. Both installed versions (7.4.11 and 7.4.15) are affected.

### Classification

This is an upstream packaging defect in the VSIX: it omits the
`@vscode/ripgrep` optional platform dependency that its bundled binary requires at runtime,
and the Morph SDK resolver throws at init instead of degrading to system `rg`.

## Evidence gathered

| Check | Result |
|---|---|
| Extension folder `node_modules/` | **Absent** (count = 0) |
| Bundled `rg` / `rg.exe` binary | **None** in extension tree |
| `@vscode/ripgrep` referenced in extension `package.json` deps | **No** |
| Error string present in `dist/extension.js` | **No** (not the extension's code) |
| Error string present in `bin/kilo` (compiled binary) | **Yes** — resolver throws it |
| `bin/kilo` file type | ELF 64-bit x86-64, Bun-compiled single executable (162 MB) |
| Two extension versions present | 7.4.11 (Jul 17), 7.4.15 (Jul 22) — both affected |
| Reusable `@vscode/ripgrep-linux-x64` on system | **Yes** — kiro-cli ships a complete copy |

### A working, reusable package already exists locally

`~/.local/share/kiro-cli/kas/2.10.0-*/node_modules/@vscode/ripgrep-linux-x64/` is a complete
package:

```
@vscode/ripgrep-linux-x64/
├── LICENSE
├── README.md
├── package.json      (name: @vscode/ripgrep-linux-x64, version: 1.18.0, os:[linux], cpu:[x64])
└── bin/
    └── rg            (ripgrep 15.0.0, 5.7 MB, executable)
```

`rg --version` → `ripgrep 15.0.0 (rev 3a612f88b8)`. No download is needed to fix this.

### Mechanism verified (non-invasive simulation)

Replicated the exact resolver call (`createRequire(pathToFileURL(...)).resolve(...)`) against
a `/tmp` mirror of the binary's layout with the package placed in a parent `node_modules/`:

```
RESOLVED: /tmp/kilorg-sim/node_modules/@vscode/ripgrep-linux-x64/bin/rg
```

Confirmed: placing the package in a `node_modules/` that `createRequire` walks up to makes the
resolve succeed — i.e. the throw disappears.

## Solution options

All three make `require.resolve('@vscode/ripgrep-linux-x64/bin/rg')` succeed from the binary's
location, which prevents the throw so the resolver returns the bundled `rg` (ripgrep 15.0.0).
After applying **any** of them, reload the window (`Developer: Reload Window`).

### Option A — Per-version symlink (uses local kiro-cli copy, no network)  *(recommended)*

Place the existing local package into the extension folder's `node_modules/`:

```bash
EXT=~/.vscode-insiders/extensions/kilocode.kilo-code-7.4.15-linux-x64
SRC=$(ls -d /home/nam20485/.local/share/kiro-cli/kas/*/node_modules/@vscode/ripgrep-linux-x64)
mkdir -p "$EXT/node_modules/@vscode"
ln -s "$SRC" "$EXT/node_modules/@vscode/ripgrep-linux-x64"
```

- **Pros:** instant, no download, no lockfile, minimal footprint.
- **Cons:** must be **re-applied after each extension upgrade** (a new version folder is
  created; the symlink lives in the old one). Re-running the snippet with the new version path
  handles it.

### Option B — Upgrade-proof symlink at a higher `node_modules` level

`createRequire` walks `node_modules` up the directory tree, so a package placed at
`~/.vscode-insiders/extensions/node_modules/` resolves for **all** Kilo Code versions at once:

```bash
EXTROOT=~/.vscode-insiders/extensions
SRC=$(ls -d /home/nam20485/.local/share/kiro-cli/kas/*/node_modules/@vscode/ripgrep-linux-x64)
mkdir -p "$EXTROOT/node_modules/@vscode"
ln -s "$SRC" "$EXTROOT/node_modules/@vscode/ripgrep-linux-x64"
```

- **Pros:** survives extension upgrades; apply once.
- **Cons:** global to all VS Code Insiders extensions (a `node_modules/` at the extensions root
  is unusual but harmless for a symlinked single package). Note: a different binary that also
  resolves `@vscode/ripgrep-linux-x64` would pick this up too — generally fine.

### Option C — `npm install` into the extension dir (network, self-contained)

```bash
EXT=~/.vscode-insiders/extensions/kilocode.kilo-code-7.4.15-linux-x64
npm install --prefix "$EXT" @vscode/ripgrep-linux-x64@1.18.0
```

- **Pros:** pulls an independent, owned copy; package-lock records it.
- **Cons:** requires network; writes a `package.json`/`package-lock.json`/`node_modules/` into
  the extension dir (may confuse the extension's own package.json on some tooling); per-version.

## Verification after applying

1. Reload window: Command Palette → `Developer: Reload Window`.
2. Trigger any code search/grep tool from the Kilo Code panel.
3. Expect: no `Could not find @vscode/ripgrep-linux-x64...` message; results return.
4. (Optional) Confirm the resolved path is the bundled one, not a throw, by re-running the
   simulation snippet against the real extension folder layout.

## Long-term fix (upstream)

The Morph SDK's `tools/warp_grep/utils/ripgrep.ts` should catch the `require.resolve` failure
and fall through to the **already-present** system-`rg` fallback instead of throwing at
module-init. Additionally, the Kilo Code VSIX should either:
- bundle `@vscode/ripgrep-linux-x64` (and other platform packages) in `bin/`, or
- ship a standalone `rg` binary in `bin/` and resolve it directly (like `bin/ffmpeg`,
  `bin/bwrap`, `bin/tree-sitter/*.wasm` are already shipped), bypassing `@vscode/ripgrep`
  entirely.

Either upstream change would make this workaround unnecessary.
