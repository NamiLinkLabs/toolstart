# toolstart — Claude Code Handoff

## What this is

A single-file Python CLI tool (`toolstart`) that:
- Stores all CLI tool secrets in one GPG-encrypted YAML file (`~/.config/toolstart/config.yaml.gpg`)
- Intercepts named commands via shell functions (e.g. `cortex`, `claude`) written into `.zshrc` / `.bashrc`
- Shows an interactive arrow-key profile picker (curses) when the user types the command
- Injects the chosen profile's env vars and `exec`s the real process — secrets never touch disk

## Current state

`./test_e2e.sh` runs an end-to-end check (list/get/hook/install/edit) with a throwaway GPG key. The multi-profile curses picker and the symmetric-passphrase `toolstart init` path need a real terminal.

## File

Single file: `toolstart.py` (Python 3.10+, shebang `#!/usr/bin/env python3`).
`toolstart install` copies it to `~/.local/bin/toolstart`, where the shell hooks invoke it.


## Architecture

```
user types: cortex -p "explain this"
        |
        v
shell function in .zshrc:
  cortex() { command toolstart hook cortex "$@" }
        |
        v
toolstart hook cortex "-p" "explain this"
  1. gpg --decrypt ~/.config/toolstart/config.yaml.gpg
  2. parse YAML, find tool "cortex", list profiles ["nonprod", "prod"]
  3. curses picker shown to user -> user picks "nonprod"
  4. merge profile env vars into os.environ copy
  5. os.execvpe("cortex", ["cortex", "-c", "nonprod", "-p", "explain this"], env)
     ^^ replaces the toolstart process entirely, secrets only in memory during step 1-4
```

## Config schema

```yaml
editor: code --wait     # used by `toolstart edit`; set by `toolstart init`, falls back to $EDITOR
# optional — omit for symmetric passphrase encryption
gpg_recipient: you@example.com

tools:
  <tool-name>:            # must match the real binary name (cortex, claude, aws...)
    profiles:
      <profile-name>:
        env:
          KEY: value      # injected into the process env
          TOK: "$(helper --flag)"  # $(cmd) replaced by its stdout at launch; quote if value has ": "
        cmd: [binary, arg1, arg2]   # list, OR a shell string ("cortex -c np")
                                    # run via sh -c; user args appended after either
```

Config path: `~/.config/toolstart/config.yaml.gpg` (override with `$TS_CONFIG`).

## Commands

- `toolstart hook <tool> [args…]` — used by shell hooks; picker → inject → exec. Don't call directly.
- `toolstart get <tool> <profile> <key>` — print one env value for `$(...)` subshell use.
- `toolstart init` — prompt for editor, create the encrypted config and point the user at `toolstart edit`.
- `toolstart edit` — decrypt config into the configured editor; detects the save by polling the temp file's mtime (does not block on a lingering editor, e.g. VS Code), validates YAML, re-encrypts on save, then auto-runs `toolstart install` so hooks match the saved tool list (skipped when nothing changed or YAML was invalid).
- `toolstart list` — show tools, profiles, cmds, env var names (never values).
- `toolstart install` — copy `toolstart.py` to `~/.local/bin/toolstart` + write hook functions to the rc file.

## Packaging / release

- `pyproject.toml` (hatchling) builds a wheel containing `toolstart.py` with the `toolstart = "toolstart:main"` entry point. `uv build` / `uv tool install toolstart`.
- When run as the packaged entry point (`__name__ != "__main__"`), `toolstart install` skips copying `toolstart.py` to `~/.local/bin` — the shim is already there.
- `.github/workflows/ci.yml`: build + wheel smoke test (ubuntu), `test_e2e.sh` (macos, needs BSD sed/stat + zsh).
- `.github/workflows/release.yml`: `workflow_dispatch` with a `bump` input runs `uv version --bump`, commits, tags `vX.Y.Z`, `uv build`, `uv publish` (PyPI trusted publishing, env `pypi`), `gh release create`. Also runs on a pushed `v*` tag (skips the bump, checks tag == pyproject version).
- `[tool.uv] publish-url` in pyproject points at TestPyPI for local `uv publish`; the workflow overrides it with `--publish-url` for real PyPI.

## Setup sequence (for the user)

```bash
pip install pyyaml
cp toolstart.py ~/.local/bin/toolstart && chmod 700 ~/.local/bin/toolstart
toolstart init           # creates ~/.config/toolstart/config.yaml.gpg, points at toolstart edit
toolstart edit           # decrypt -> $EDITOR -> re-encrypt; add real secrets here (auto-runs toolstart install on save)
source ~/.zshrc
cortex            # picker appears
```

## Known issues / things to verify on a real machine

- **`toolstart install` idempotency**: strips old hook block via regex before re-writing. Needs testing when tool list changes between installs.
- **Single-profile auto-skip**: if a tool has exactly one profile, `pick_profile` returns it immediately without showing the picker. This is intentional.
- **`toolstart install` shell detection**: reads `$SHELL` env var. If the user's login shell differs from their active shell, the wrong rc file could be targeted.
- **Python version**: uses `str | None` union syntax which requires Python 3.10+. Should add a version guard at the top or rewrite as `Optional[str]` for broader compat.

## Potential improvements

- `toolstart add <tool>` interactive wizard to add a new tool without opening the raw YAML
- `toolstart rotate <tool> <profile>` to update a single secret value
- Support `age` encryption as an alternative to GPG (simpler UX)
- Numbered key shortcuts in the picker (press `1`, `2` to select by index)
- `TS_SKIP_PICKER=nonprod cortex` env var to bypass the picker in scripts
