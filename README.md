# ts — toolstart

GPG-encrypted secret injector with an interactive profile picker.

`ts` keeps all your CLI tool secrets (API keys, PATs, tokens) in a single
GPG-encrypted YAML file. Shell hooks intercept the commands you type
(`cortex`, `claude`, `aws`, …), show an arrow-key profile picker, inject the
chosen profile's environment variables, and `exec` the real binary — secrets
live only in memory, never on disk in plaintext.

## Requirements

- Python 3.10+
- [GPG](https://gnupg.org/) (`gpg` on PATH)
- [PyYAML](https://pypi.org/project/PyYAML/) — `pip install pyyaml`

## Install

```bash
pip install pyyaml
cp ts.py ~/.local/bin/ts && chmod 700 ~/.local/bin/ts   # ensure ~/.local/bin is on PATH
```

## Quick start

```bash
ts init       # creates ~/.config/toolstart/config.yaml.gpg (GPG passphrase prompt)
ts edit       # opens decrypted config in $EDITOR, re-encrypts on save and installs/updates hooks
source ~/.zshrc

cortex        # profile picker appears, secrets injected, real tool runs
```

`ts edit` auto-runs `ts install` after a successful save, so hooks always
match your tool list.

## Commands

| Command | Description |
|---------|-------------|
| `ts hook <tool> [args…]` | Used by shell hooks — shows picker, injects secrets, execs the tool. Don't call directly. |
| `ts init` | Create an empty encrypted config, then point you at `ts edit`. |
| `ts edit` | Decrypt config into `$EDITOR`, validate YAML, re-encrypt on save, then auto-run `ts install`. Temp file is wiped afterwards. |
| `ts list` | Show configured tools, profiles, base commands, and env var *names* (values are never printed). |
| `ts install` | Copy `ts.py` to `~/.local/bin` and add hook functions for each configured tool to `.zshrc` / `.bashrc`. Safe to re-run; replaces the previous hook block. |
| `ts --help` | Show help. |

## Config format

Stored at `~/.config/toolstart/config.yaml.gpg` (override with `$TS_CONFIG`).

```yaml
# Optional — omit for symmetric passphrase encryption.
# When set, config is encrypted to that public key instead.
gpg_recipient: you@example.com

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
- `cmd` is the base command — a list, or a shell string (`"cortex -c np"`, run
  via `sh -c`); anything you type after the tool name is appended:
  `cortex -p "hi"` → `cortex -c nonprod -p "hi"`.
- If a tool has exactly **one** profile, the picker is skipped and it runs
  immediately.

## How it works

```
user types: cortex -p "explain this"
        ↓
shell function (.zshrc):  cortex() { command ts hook cortex "$@" }
        ↓
ts hook cortex -p "explain this"
  1. gpg --decrypt config
  2. parse YAML, list profiles
  3. curses picker → user picks "nonprod"
  4. merge profile env into os.environ copy
  5. os.execvpe replaces the ts process with the real tool
```

Secrets exist in memory only between steps 1–4. Nothing is written to disk or
printed.

## Security notes

- The config file is chmod `600` after every encrypt.
- `ts edit` wipes its temp file before deletion.
- Cancel the picker (`q`, `Esc`, `Ctrl-C`) and the shell prompt returns without
  running anything.
- For public-key (asymmetric) encryption, set `gpg_recipient` in the config —
  useful for automation where no passphrase prompt is possible.
- `ts list` deliberately prints only env var names, never values.

## Troubleshooting

- **`ts: GPG decryption failed`** — wrong passphrase, or your key isn't
  available. Test with `gpg --decrypt ~/.config/toolstart/config.yaml.gpg`.
- **Picker doesn't appear / hooks not firing** — re-run `ts install`, then
  `source ~/.zshrc` (or `.bashrc`).
- **Wrong rc file updated** — `ts install` picks the rc file from `$SHELL`.
- **Colors look wrong in the picker** — remove the `curses.init_pair` /
  `curses.color_pair(1)` lines in `pick_profile` (see known issues in
  `AGENTS.md`).
