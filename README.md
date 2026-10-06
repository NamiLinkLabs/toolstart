# toolstart

GPG-encrypted secret injector with an interactive profile picker.

`toolstart` keeps all your CLI tool secrets (API keys, PATs, tokens) in a single
GPG-encrypted YAML file. Shell hooks intercept the commands you type
(`cortex`, `claude`, `aws`, …), show an arrow-key profile picker, inject the
chosen profile's environment variables, and `exec` the real binary — secrets
live only in memory, never on disk in plaintext.

## Requirements

- Python 3.10+
- [GPG](https://gnupg.org/) (`gpg` on PATH; Windows: [Gpg4win](https://gpg4win.org/))
- macOS / Linux (zsh, bash) or Windows (PowerShell 7 / Windows PowerShell 5.1)
- tkinter, for the `toolstart edit` window — bundled with python.org and uv-managed Pythons;
  Homebrew: `brew install python-tk`, Debian/Ubuntu: `sudo apt install python3-tk`

## Install

Recommended — isolated env, `toolstart` binary in `~/.local/bin`:

```bash
uv tool install toolstart      # update: uv tool upgrade toolstart
# or
pipx install toolstart         # update: pipx upgrade toolstart
```

Plain pip also works (`pip install --user toolstart`, update with `pip install -U toolstart`),
but system Pythons on Debian/Ubuntu/Homebrew refuse installs outside a venv (PEP 668),
and macOS puts user scripts in `~/Library/Python/3.x/bin` — make sure that dir is on PATH.

From a checkout: `uv tool install .` (editable for development: `uv tool install -e . --force`)

### Windows (PowerShell)

PowerShell only loads `$PROFILE` (where the hooks live) if scripts are allowed:

```powershell
Get-ExecutionPolicy -List                               # MachinePolicy/UserPolicy must be Undefined
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

If `MachinePolicy` or `UserPolicy` is `Restricted` / `AllSigned`, group policy enforces it
and the hooks can't load on that machine.

Then install `gpg`, `uv` and toolstart with one of these blocks (paste it as a whole;
uv downloads a Python itself if none is installed):

<details>
<summary><b>winget</b> (built into Windows 10/11)</summary>

```powershell
winget install GnuPG.Gpg4win      # gpg (installer needs admin)
winget install astral-sh.uv
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
uv tool install toolstart
uv tool update-shell              # puts uv's tool bin dir on PATH for new terminals
gpg --version
```

`0x8a15000f : Data required by the source is missing` → the winget source index is broken:
run `winget source reset --force` in an **admin** terminal, then `winget source update`.
No admin rights? Use scoop instead.

</details>

<details>
<summary><b>scoop</b> (per-user, no admin needed)</summary>

```powershell
irm get.scoop.sh | iex            # installs scoop itself (needs the execution policy above)
scoop install gpg uv
uv tool install toolstart
uv tool update-shell              # puts uv's tool bin dir on PATH for new terminals
gpg --version
```

</details>

Then open a **new** terminal:

```powershell
toolstart init; toolstart edit
. $PROFILE                        # or open a new terminal
```

PyYAML is installed automatically as a dependency; nothing else is needed.

**Who owns the binary?** Whatever installed it. `uv tool` / `pipx` / `pip` write the
`toolstart` entry point and replace it on upgrade; `toolstart install` only (re)writes the
shell hooks. Hooks call `toolstart` from PATH, so any bin dir on PATH works.

**Updates.** At most every 6 hours `toolstart hook` asks PyPI for the latest version
(cached in `~/.cache/toolstart/update-check.json`, 2 s timeout, silent when offline).
If a newer one exists, a prompt appears before the profile picker: **install** (upgrades
via uv tool / pipx / pip, then restarts the picker) or **skip this version** (asks again
only for a newer release); `Esc` = ask later.
Untick *Check PyPI for new toolstart versions* in `toolstart edit` › settings to turn it
off — the update code is then not even imported.

## Quick start

```bash
toolstart init       # pick the GPG key to encrypt to (or create one / use a passphrase)
toolstart edit       # window: add tools, profiles, secrets; Save encrypts and installs the hooks
source ~/.zshrc      # PowerShell: . $PROFILE

cortex        # profile picker appears, secrets injected, real tool runs
```

### `toolstart edit`

A window with your tools and profiles on the left and the selected item's form on the right:

- **settings** — which GPG key the config is encrypted to, the update check, and global env vars.
- **a profile** — the command to run and its env vars. Values are masked (`••••`) until you
  press *show*. `+ variable` adds a row, `✕` removes one.
- **+ tool / + profile / rename / delete** on the left. A new tool starts with a `default`
  profile that runs the tool itself.
- **Save** (`Ctrl+S`, macOS `Cmd+S`) checks the names, encrypts the config and re-installs the
  hooks. A problem (empty or duplicate name, profile without a command) is shown at the
  bottom and the window stays open with your edits.

The config is edited in memory only — no temp file, no plaintext on disk.

## Commands

| Command | Description |
|---------|-------------|
| `toolstart hook <tool> [args…]` | Used by shell hooks — shows picker, injects secrets, launches the tool. Don't call directly. |
| `toolstart init` | Pick the GPG key to encrypt to from a menu of your secret keys — or create a new key (`gpg --quick-gen-key`), or use a symmetric passphrase. Creates an empty encrypted config. |
| `toolstart edit` | Edit tools, profiles and secrets in a window (see above). Save re-encrypts and runs `toolstart install`. |
| `toolstart list` | Show configured tools, profiles, base commands, and env var *names* (values are never printed). |
| `toolstart install` | Add hook functions for each configured tool to `.zshrc` / `.bashrc` (from `$SHELL`), or on Windows to the `$PROFILE` of every installed PowerShell (`pwsh`, `powershell`). Safe to re-run; replaces the previous hook block. |
| `toolstart --help` | Show help. |

## Config format

Stored at `~/.config/toolstart/config.yaml.gpg` (override with `$TS_CONFIG`). You don't edit
it directly — `toolstart edit` does. For reference, this is what it holds:

```yaml
gpg_recipient: 3F2A…C91D          # key fingerprint; absent = symmetric passphrase
env:                              # global env, merged into every profile
  OPENCODE_ENABLE_EXA: "1"
tools:
  cortex:                         # the hook intercepts the `cortex` command
    profiles:
      np:
        cmd: cortex -c np         # base command; your args are appended
        env:
          SNOWFLAKE_CONNECTIONS_NP_PASSWORD: pat-nonprod
```

- A tool's name must match the real binary name — that's what the shell hook
  shadows.
- Global env (settings) is merged into every profile's environment; a profile's own
  value wins. `toolstart list` shows global env var
  names, and `toolstart get` falls back to them.
- The command is a shell string (`cortex -c np`, run
  via `sh -c`, on Windows via `cmd.exe`); anything you type after the tool name is appended:
  `cortex -p "hi"` → `cortex -c nonprod -p "hi"`.
  On Windows that means cmd.exe syntax even when you launch from PowerShell: reference a
  variable in the command or a `$(...)` helper as `%KEY%`, not `$env:KEY`
  (`echo %KEY%`). Plain commands like `claude` don't care — the env vars are
  injected into the tool either way.
- If a tool has exactly **one** profile, the picker is skipped and it runs
  immediately.

### Values fetched by a command: `$(...)`

Some secrets are short-lived and come from a helper command instead of being
stored. Put the command in `$(...)` as the variable's value:

| Variable | Value |
|---|---|
| `ANTHROPIC_AUTH_TOKEN` | `$(okta_auth --helper claude --org sales --quiet)` |
| `CUSTOM_HEADER` | `Bearer $(cat ~/.token)` — can be part of a longer value |

When you type `claude` and pick `sales`, toolstart runs the helper command and uses what
it prints as the value of `ANTHROPIC_AUTH_TOKEN`, then starts `claude`.

- The command runs each time you launch the tool, only for the profile you pick.
  It runs via `/bin/sh` (Windows: `cmd.exe`).
- Prompts from the helper (MFA, login) show up in your terminal as normal.
- If the helper fails (non-zero exit), toolstart stops and the tool is not started.
- Only `$(...)` is run. `$VAR` stays literal text, so secrets containing `$`
  are safe.
- `toolstart get <tool> <profile> <key>` also runs the command and prints the result.

## How it works

```
user types: cortex -p "explain this"
        ↓
shell function (.zshrc):  cortex() { command toolstart hook cortex "$@" }
        ↓
toolstart hook cortex -p "explain this"
  1. gpg --decrypt config
  2. parse YAML, list profiles
  3. arrow-key picker (plain ANSI, no curses) → user picks "nonprod"
  4. merge global + profile env into os.environ copy
  5. os.execvpe replaces the toolstart process with the real tool
   (Windows has no exec: the tool runs as a child, toolstart passes its exit code through)
```

Secrets exist in memory only between steps 1–4. Nothing is written to disk or
printed.

## Security notes

- The config file is chmod `600` after every encrypt.
- `toolstart edit` keeps the decrypted config in memory; the plaintext reaches `gpg` through
  a pipe, never a file. The new config replaces the old one only after `gpg` succeeded.
- Cancel the picker (`q`, `Esc`, `Ctrl-C`) and the shell prompt returns without
  running anything.
- **Passphrases are asked by toolstart, not by a GPG popup** (those open behind the terminal
  on Windows): in the terminal for `cortex` & co., in a masked dialog in `toolstart edit`.
  toolstart first tries without one — if gpg-agent has it cached (or the key has none) you're
  not asked at all; a wrong one is asked again (3 tries). It's handed to `gpg` through a pipe
  (loopback mode), never via the command line or a file.
- Prefer a key (`gpg_recipient`) over a symmetric passphrase: saving then needs no passphrase,
  and the key's passphrase is cached by gpg-agent after the first use. A symmetric passphrase
  isn't cached in this mode — it's asked on **every** launch, and twice on every save.
- **Lock** (forget cached passphrases): `gpgconf --reload gpg-agent`. Cache length is set in
  `gpg-agent.conf` (`default-cache-ttl`, `max-cache-ttl`; folder: `gpgconf --list-dirs homedir`).
- `toolstart list` deliberately prints only env var names, never values.

## Troubleshooting

- **`toolstart: GPG decryption failed`** — wrong passphrase, or your key isn't
  available. Test with `gpg --decrypt ~/.config/toolstart/config.yaml.gpg`.
- **Picker doesn't appear / hooks not firing** — re-run `toolstart install`, then
  `source ~/.zshrc` (or `.bashrc`; PowerShell: `. $PROFILE`).
- **Wrong rc file updated** — `toolstart install` picks the rc file from `$SHELL`.
- **`toolstart edit`: needs tkinter** — install it (see Requirements) or reinstall on uv's own
  Python: `uv tool install --python-preference only-managed --force toolstart`.
- **Mistakes in a config written by hand** (before `toolstart edit` had a window: `KEY:value`
  without the space, `env:` not indented, …) are reported with the tool/profile they're in —
  never with the value. Fix them by hand once, then use `toolstart edit`:
  `gpg -d ~/.config/toolstart/config.yaml.gpg > plain.yaml`, edit, then
  `gpg -e -r <your key> -o ~/.config/toolstart/config.yaml.gpg --yes plain.yaml` and delete `plain.yaml`.
- **`toolstart: unexpected error — …`** — a bug; rerun with `TS_DEBUG=1` for the full
  traceback and open an issue.

## Releasing

Versions live in `pyproject.toml` and are bumped with `uv version`.

- **From GitHub**: Actions → *Release* → *Run workflow* → pick `patch` / `minor` / `major`.
  The workflow bumps the version, commits, tags `vX.Y.Z`, builds with `uv build`,
  publishes to PyPI via trusted publishing, and creates a GitHub release with the artifacts.
- **Locally**:
  ```bash
  uv version --bump patch
  git commit -am "release: v$(uv version --short)"
  git tag "v$(uv version --short)" && git push && git push --tags   # tag push triggers the same workflow
  ```

One-time setup: on PyPI, add a trusted publisher for this repo
(workflow `release.yml`, environment `pypi`) and create the `pypi` environment in the GitHub repo settings.
