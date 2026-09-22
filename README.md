# toolstart

GPG-encrypted secret injector with an interactive profile picker.

`toolstart` keeps all your CLI tool secrets (API keys, PATs, tokens) in a single
GPG-encrypted YAML file. Shell hooks intercept the commands you type
(`cortex`, `claude`, `aws`, …), show an arrow-key profile picker, inject the
chosen profile's environment variables, and `exec` the real binary — secrets
live only in memory, never on disk in plaintext.

## Requirements

- Python 3.10+
- [GPG](https://gnupg.org/) (`gpg` on PATH)
- [PyYAML](https://pypi.org/project/PyYAML/) — `pip install pyyaml`

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

From source, no package (single file):

```bash
pip install pyyaml
cp toolstart.py ~/.local/bin/toolstart && chmod 700 ~/.local/bin/toolstart   # ensure ~/.local/bin is on PATH
```

**Who owns the binary?** Whatever installed it. `uv tool` / `pipx` / `pip` write the
`toolstart` entry point and replace it on upgrade; `toolstart install` never touches it
in that case — it only (re)writes the shell hooks. The self-copy to `~/.local/bin` only
happens when you run `toolstart.py` straight from a checkout. Hooks call `command toolstart`,
so any bin dir on PATH works.

## Quick start

```bash
toolstart init       # asks which editor to use, creates ~/.config/toolstart/config.yaml.gpg (GPG passphrase prompt)
toolstart edit       # opens decrypted config in that editor, re-encrypts on save and installs/updates hooks
source ~/.zshrc

cortex        # profile picker appears, secrets injected, real tool runs
```

`toolstart edit` auto-runs `toolstart install` after a successful save, so hooks always
match your tool list.

## Commands

| Command | Description |
|---------|-------------|
| `toolstart hook <tool> [args…]` | Used by shell hooks — shows picker, injects secrets, execs the tool. Don't call directly. |
| `toolstart init` | Ask for your editor, create an empty encrypted config, then point you at `toolstart edit`. |
| `toolstart edit` | Decrypt config into your editor. Detects the save by polling the temp file's mtime, so it validates, re-encrypts, auto-runs `toolstart install`, and exits as soon as you save — even if the editor (e.g. VS Code) keeps running. Temp file is wiped afterwards. |
| `toolstart list` | Show configured tools, profiles, base commands, and env var *names* (values are never printed). |
| `toolstart install` | Add hook functions for each configured tool to `.zshrc` / `.bashrc`. Safe to re-run; replaces the previous hook block. When run from a checkout (not a package install) it also copies `toolstart.py` to `~/.local/bin`. |
| `toolstart --help` | Show help. |

## Config format

Stored at `~/.config/toolstart/config.yaml.gpg` (override with `$TS_CONFIG`).

```yaml
# Editor for `toolstart edit`. Set by `toolstart init`; falls back to $EDITOR, then `code --wait`.
editor: code --wait

# Optional — omit for symmetric passphrase encryption.
# When set, config is encrypted to that public key instead.
gpg_recipient: you@example.com

# Optional — env vars applied to every tool/profile.
# A profile's own `env:` overrides these.
env:
  OPENCODE_ENABLE_EXA: "1"

tools:
  cortex:                        # hook intercepts the 'cortex' command
    profiles:
      np:
        env:                     # injected into the process environment
          SNOWFLAKE_CONNECTIONS_NP_PASSWORD: pat-nonprod
        cmd: cortex -c np   # base command; user args are appended

  claude:
    profiles:
      default:
        env:
          ANTHROPIC_API_KEY: sk-ant-...
        cmd: [claude]
```

- `tools.<name>` must match the real binary name — that's what the shell hook
  shadows.
- Top-level `env:` (optional) is merged into every profile's environment;
  keys defined in a profile's own `env:` win. `toolstart list` shows global env var
  names, and `toolstart get` falls back to them.
- `cmd` is the base command — a list, or a shell string (`"cortex -c np"`, run
  via `sh -c`); anything you type after the tool name is appended:
  `cortex -p "hi"` → `cortex -c nonprod -p "hi"`.
- If a tool has exactly **one** profile, the picker is skipped and it runs
  immediately.

### Values fetched by a command: `$(...)`

Some secrets are short-lived and come from a helper command instead of being
stored. Wrap the command in `$(...)` and **put the whole value in double
quotes**:

```yaml
tools:
  claude:
    profiles:
      sales:
        env:
          ANTHROPIC_AUTH_TOKEN: "$(okta_auth --helper claude --org sales --quiet)"
          CUSTOM_HEADER: "Bearer $(cat ~/.token)"   # can be part of a longer value
        cmd: claude
```

When you type `claude` and pick `sales`, toolstart runs the helper command and uses what
it prints as the value of `ANTHROPIC_AUTH_TOKEN`, then starts `claude`.

- The command runs each time you launch the tool, only for the profile you pick.
- Prompts from the helper (MFA, login) show up in your terminal as normal.
- If the helper fails (non-zero exit), toolstart stops and the tool is not started.
- Only `$(...)` is run. `$VAR` stays literal text, so secrets containing `$`
  are safe.
- Always quote. Without quotes, a `: ` (e.g. `--flag: x`) or ` #` inside the
  command breaks the YAML: it fails with
  `mapping values are not allowed here` and `toolstart edit` refuses to save.
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
  3. curses picker → user picks "nonprod"
  4. merge global + profile env into os.environ copy
  5. os.execvpe replaces the toolstart process with the real tool
```

Secrets exist in memory only between steps 1–4. Nothing is written to disk or
printed.

## Security notes

- The config file is chmod `600` after every encrypt.
- `toolstart edit` wipes its temp file before deletion.
- Cancel the picker (`q`, `Esc`, `Ctrl-C`) and the shell prompt returns without
  running anything.
- For public-key (asymmetric) encryption, set `gpg_recipient` in the config —
  useful for automation where no passphrase prompt is possible.
- `toolstart list` deliberately prints only env var names, never values.

## Troubleshooting

- **`toolstart: GPG decryption failed`** — wrong passphrase, or your key isn't
  available. Test with `gpg --decrypt ~/.config/toolstart/config.yaml.gpg`.
- **Picker doesn't appear / hooks not firing** — re-run `toolstart install`, then
  `source ~/.zshrc` (or `.bashrc`).
- **Wrong rc file updated** — `toolstart install` picks the rc file from `$SHELL`.

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
