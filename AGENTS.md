# toolstart — Claude Code Handoff

## What this is

A small Python CLI tool (`toolstart`) that:
- Stores all CLI tool secrets in one GPG-encrypted YAML file (`~/.config/toolstart/config.yaml.gpg`)
- Lets the user edit tools/profiles/secrets in a tkinter window (`toolstart edit`) — the YAML is never shown and never written in plaintext
- Intercepts named commands via shell functions (e.g. `cortex`, `claude`) written into `.zshrc` / `.bashrc` (Windows: PowerShell `$PROFILE`)
- Shows an interactive arrow-key profile picker (plain ANSI + raw key reads, no curses) when the user types the command
- Injects the chosen profile's env vars and `exec`s the real process — secrets never touch disk

## Current state

- `tests/test_e2e.sh` — list/get/hook/install/save_config + malformed configs, with a throwaway GPG key.
- `python tests/test_picker.py` — picker, update prompt and `init` through a pty (needs the package installed, e.g. in a venv from the built wheel; run it outside the checkout).
- `python tests/test_gui.py` — drives the `toolstart edit` window programmatically (needs tkinter + a display).
- Everything Windows-specific is untested locally.

## Files

Package `toolstart/` (Python 3.10+, keep the total under ~1000 lines):
- `cli.py` — config/GPG, `choose()` menu, commands, `main()` (entry point `toolstart.cli:main`; `python -m toolstart` via `__main__.py`)
- `gui.py` — the `toolstart edit` window (`Editor`, `EnvTable`); imported only by `cmd_edit`. `ask`/`confirm` are attributes so tests can replace the dialogs.
- `_posix.py` / `_win.py` — only the current platform's module is imported. Each provides `read_key()`, `launch(cmd, args, env)`, `hook_files(tools)`, `RELOAD`.
- `update.py` — PyPI update check, imported only when config `update_check` is not false.

Install only as a package (uv tool / pipx / pip); `toolstart install` only writes hooks.

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
  3. picker shown to user -> user picks "nonprod"
  4. merge profile env vars into os.environ copy
  5. os.execvpe("cortex", ["cortex", "-c", "nonprod", "-p", "explain this"], env)
     ^^ replaces the toolstart process entirely, secrets only in memory during step 1-4
     (Windows: no exec — child process via subprocess, exit code passed through)
```

## Config schema (written by `write_config` via `yaml.safe_dump`)

```yaml
gpg_recipient: <fingerprint>   # absent = symmetric passphrase
update_check: false            # only written when turned off
env: {KEY: value}              # global env, merged under every profile's env
tools:
  <tool-name>:                 # must match the real binary name (cortex, claude, aws...)
    profiles:
      <profile-name>:
        cmd: cortex -c np      # shell string (sh -c / Windows cmd.exe); a list (exec, no shell) from older configs is kept as long as it isn't edited
        env:
          KEY: value
          TOK: "$(helper --flag)"   # $(cmd) replaced by its stdout at launch
```

Config path: `~/.config/toolstart/config.yaml.gpg` (override with `$TS_CONFIG`).

## Commands

- `toolstart hook <tool> [args…]` — used by shell hooks; picker → inject → exec. Don't call directly.
  Update check (unless `update_check: false`): PyPI at most every 6h (cache `~/.cache/toolstart/update-check.json` holds `checked`/`latest`/`skipped`). If newer and not skipped, a modal before the picker (also for single-profile tools): install → upgrade via uv tool / pipx / pip → relaunch `python -m toolstart hook …`; skip this version → stored in `skipped`; Esc → ask next time. Not offered when no package metadata (source checkout).
- `toolstart get <tool> <profile> <key>` — print one env value for `$(...)` subshell use.
- `toolstart init` — `choose()` a GPG secret key (from `gpg --list-secret-keys --with-colons`, stored as `gpg_recipient: <fpr>`), or create one (`gpg --quick-gen-key <uid>`), or symmetric; writes an empty config.
- `toolstart edit` — opens `gui.Editor`, raises it, then loads the config (passphrase dialog if needed). Save validates (names, every profile has a cmd), then `save_config` → `write_config` (plaintext piped to gpg, output to `<config>.new`, `os.replace` on success) → `cmd_install(tools)`. A failed save (e.g. cancelled passphrase) leaves the window open with the edits.
- `toolstart list` — show tools, profiles, cmds, env var names (never values).
- `toolstart install` — write hook functions to the rc file (POSIX, from `$SHELL`) or to the `$PROFILE` of each installed `pwsh`/`powershell`.

## Error handling

- `load_config()` runs `check_config()` (for configs written by hand before the GUI): every level (tools, profiles, env) must be a mapping, `cmd` a str/list; levels are normalized to `{}` so later code can index directly. Errors name the location (`'tool/profile' env:`), add a hint for `KEY:value` (missing space), and never print values (may be secrets). YAML errors show line/column + problem only, not the quoted line.
- `main()` turns any other exception into one line; `TS_DEBUG=1` re-raises for the traceback.
- Passphrases: never a pinentry popup (Windows opens it behind the terminal/window). `gpg_pass()` runs gpg with `--batch --pinentry-mode loopback`, first without a passphrase (agent cache / no passphrase), and only when stderr says one is needed (`can't get input`, `Bad passphrase`, symmetric: `Bad session key`) calls `ask(prompt)` and retries with `--passphrase-fd 0` (passphrase = first stdin line, data follows). `ask` = `ask_terminal` (getpass) in the CLI, `Editor.ask_secret` (masked Tk dialog) in the editor; `new_passphrase()` asks twice. Key passphrases get cached by gpg-agent in loopback mode; symmetric ones don't.
- `gui.use_base_tcl()`: in a venv on uv's standalone Python (what `uv tool install` creates) Tk fails with "Can't find a usable init.tcl"; it points `TCL_LIBRARY`/`TK_LIBRARY` at `sys.base_prefix`.

## Packaging / release

- `pyproject.toml` (hatchling) builds a wheel of the `toolstart` package with the `toolstart = "toolstart.cli:main"` entry point. `uv build` / `uv tool install toolstart`.
- `.github/workflows/ci.yml`: build + wheel smoke test (windows), `tests/test_e2e.sh` + `tests/test_picker.py` + `tests/test_gui.py` (macos, needs BSD sed/stat + zsh).
- `.github/workflows/release.yml`: `workflow_dispatch` with a `bump` input runs `uv version --bump`, commits, tags `vX.Y.Z`, `uv build`, `uv publish` (PyPI trusted publishing, env `pypi`), `gh release create`. Also runs on a pushed `v*` tag (skips the bump, checks tag == pyproject version).
- `[tool.uv] publish-url` in pyproject points at TestPyPI for local `uv publish`; the workflow overrides it with `--publish-url` for real PyPI.

## Setup sequence (for the user)

```bash
uv tool install toolstart   # or pipx / pip
toolstart init           # GPG key menu, creates ~/.config/toolstart/config.yaml.gpg
toolstart edit           # window: add tools/profiles/secrets; Save encrypts + installs hooks
source ~/.zshrc          # PowerShell: . $PROFILE
cortex            # picker appears
```

## Known issues / things to verify on a real machine

- **`toolstart install` shell detection**: reads `$SHELL` env var. If the user's login shell differs from their active shell, the wrong rc file could be targeted.
- **Single-profile auto-skip**: if a tool has exactly one profile, `cmd_hook` uses it immediately without showing the picker. This is intentional.
- **Windows (untested)**: self-upgrade while the `toolstart.exe` shim is running may hit a file lock; a `$PROFILE` saved as UTF-16 by PowerShell 5.1 can't be read as UTF-8 by `toolstart install`.
- **Editor window**: env rows don't scroll — a profile with very many variables needs a taller window.

## Potential improvements

- Paste a `.env` block into a profile in the editor
- Support `age` encryption as an alternative to GPG (simpler UX)
- Numbered key shortcuts in the picker (press `1`, `2` to select by index)
- `TS_SKIP_PICKER=nonprod cortex` env var to bypass the picker in scripts
