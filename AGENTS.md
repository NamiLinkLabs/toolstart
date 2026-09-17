# toolstart (`ts`) — Claude Code Handoff

## What this is

A single-file Python CLI tool (`ts`) that:
- Stores all CLI tool secrets in one GPG-encrypted YAML file (`~/.config/toolstart/config.yaml.gpg`)
- Intercepts named commands via shell functions (e.g. `cortex`, `claude`) written into `.zshrc` / `.bashrc`
- Shows an interactive arrow-key profile picker (curses) when the user types the command
- Injects the chosen profile's env vars and `exec`s the real process — secrets never touch disk

## Current state

The script is complete and syntax-checked. It needs to be tested end-to-end on a real machine with gpg and real tools present.

## File

Single file: `ts` (Python 3.10+, shebang `#!/usr/bin/env python3`)

### Full source

```python
#!/usr/bin/env python3
"""
ts — toolstart
==============
GPG-encrypted secret injector with interactive profile picker.

Commands:
  ts hook <tool> [args...]    Called by shell hooks — shows profile picker,
                              injects secrets, execs the tool.
  ts edit                     Decrypt config in $EDITOR and re-encrypt.
  ts list                     List configured tools and profiles.
  ts install                  Install shell hooks into your rc file.
  ts init                     Create an empty encrypted config.

Config: ~/.config/toolstart/config.yaml.gpg  (override: $TS_CONFIG)

Config format:
  tools:
    cortex:                          # hook intercepts the 'cortex' command
      profiles:
        nonprod:
          env:
            SF_NP: pat-nonprod
            SF_P:  pat-prod
          cmd: [cortex, -c, nonprod]
        prod:
          env:
            SF_NP: pat-nonprod
            SF_P:  pat-prod
          cmd: [cortex, -c, prod]

    claude:
      profiles:
        default:
          env:
            ANTHROPIC_API_KEY: sk-ant-...
          cmd: [claude]
"""

import os
import sys
import subprocess
import tempfile
import shutil
import stat
import curses

try:
    import yaml
except ImportError:
    sys.exit("ts: requires PyYAML — run: pip install pyyaml")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SELF = os.path.realpath(__file__)
DEFAULT_CONFIG = os.path.expanduser("~/.config/toolstart/config.yaml.gpg")
CONFIG_PATH = os.environ.get("TS_CONFIG", DEFAULT_CONFIG)

# ---------------------------------------------------------------------------
# GPG
# ---------------------------------------------------------------------------

def gpg_decrypt(path: str) -> str:
    r = subprocess.run(
        ["gpg", "--quiet", "--batch", "--decrypt", path],
        capture_output=True,
    )
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip()
        sys.exit(f"ts: GPG decryption failed:\n  {err}")
    return r.stdout.decode()


def gpg_encrypt(plaintext: str, path: str, recipient: str | None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if recipient:
        cmd = ["gpg", "--quiet", "--batch", "--yes",
               "--encrypt", "--recipient", recipient, "--output", path]
    else:
        cmd = ["gpg", "--quiet", "--batch", "--yes",
               "--symmetric", "--cipher-algo", "AES256", "--output", path]
    r = subprocess.run(cmd, input=plaintext.encode(), capture_output=True)
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip()
        sys.exit(f"ts: GPG encryption failed:\n  {err}")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        sys.exit(
            f"ts: no config found at {CONFIG_PATH}\n"
            "    Run: ts init"
        )
    raw = gpg_decrypt(CONFIG_PATH)
    try:
        return yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        sys.exit(f"ts: invalid YAML in config: {e}")


def get_tool(config: dict, name: str) -> dict:
    tools = config.get("tools", {})
    if name not in tools:
        available = ", ".join(tools) if tools else "(none)"
        sys.exit(f"ts: unknown tool '{name}'.  Configured: {available}")
    return tools[name]


# ---------------------------------------------------------------------------
# Interactive profile picker (curses)
# ---------------------------------------------------------------------------

def pick_profile(tool_name: str, profiles: list[str]) -> str | None:
    """
    Show an arrow-key selector in the terminal.
    Returns the chosen profile name, or None if the user pressed q/Escape/Ctrl-C.
    """
    if len(profiles) == 1:
        return profiles[0]

    def _picker(stdscr):
        curses.curs_set(0)
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)

        idx = 0

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()

            header = f" ts › {tool_name} — select profile (↑↓ enter, q=quit) "
            stdscr.addstr(0, 0, header[:w], curses.A_BOLD)

            for i, name in enumerate(profiles):
                row = i + 2
                if row >= h:
                    break
                label = f"  {'›' if i == idx else ' '} {name}"
                if i == idx:
                    stdscr.addstr(row, 0, label[:w], curses.color_pair(1))
                else:
                    stdscr.addstr(row, 0, label[:w])

            stdscr.refresh()
            key = stdscr.getch()

            if key in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(profiles)
            elif key in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(profiles)
            elif key in (curses.KEY_ENTER, 10, 13):
                return profiles[idx]
            elif key in (ord("q"), ord("Q"), 27):   # 27 = Escape
                return None

    try:
        return curses.wrapper(_picker)
    except KeyboardInterrupt:
        return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_hook(tool_name: str, extra_args: list[str]) -> None:
    config = load_config()
    tool = get_tool(config, tool_name)
    profiles = tool.get("profiles", {})

    if not profiles:
        sys.exit(f"ts: tool '{tool_name}' has no profiles defined.")

    profile_names = list(profiles)
    chosen = pick_profile(tool_name, profile_names)

    if chosen is None:
        # User cancelled — exit cleanly so the shell prompt returns normally.
        sys.exit(0)

    profile = profiles[chosen]
    env = os.environ.copy()
    for k, v in (profile.get("env") or {}).items():
        env[str(k)] = str(v)

    base_cmd = profile.get("cmd")
    if not base_cmd:
        sys.exit(f"ts: profile '{chosen}' in tool '{tool_name}' has no 'cmd'.")

    full_cmd = list(base_cmd) + extra_args

    try:
        os.execvpe(full_cmd[0], full_cmd, env)
    except FileNotFoundError:
        sys.exit(f"ts: command not found: {full_cmd[0]}")


def cmd_list() -> None:
    config = load_config()
    tools = config.get("tools", {})
    if not tools:
        print("ts: no tools configured.")
        return
    for tname, tval in tools.items():
        profiles = tval.get("profiles", {})
        print(f"  {tname}")
        for pname, pval in profiles.items():
            cmd = " ".join(pval.get("cmd") or [])
            env_keys = ", ".join((pval.get("env") or {}).keys())
            print(f"    {pname:<14}  cmd: {cmd}")
            if env_keys:
                print(f"    {'':14}  env: {env_keys}")


def cmd_edit() -> None:
    editor = os.environ.get("EDITOR", "nano")
    recipient = None

    if os.path.exists(CONFIG_PATH):
        raw = gpg_decrypt(CONFIG_PATH)
        try:
            existing = yaml.safe_load(raw) or {}
            recipient = existing.get("gpg_recipient")
        except yaml.YAMLError:
            raw = _EMPTY_CONFIG
    else:
        raw = _EMPTY_CONFIG

    fd, tmp = tempfile.mkstemp(suffix=".yaml", prefix="toolstart-")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(raw)

        mtime_before = os.path.getmtime(tmp)
        subprocess.run([editor, tmp])
        mtime_after = os.path.getmtime(tmp)

        if mtime_before == mtime_after:
            print("ts: no changes, config unchanged.")
            return

        with open(tmp) as f:
            new_content = f.read()

        try:
            parsed = yaml.safe_load(new_content) or {}
        except yaml.YAMLError as e:
            sys.exit(f"ts: invalid YAML — config NOT saved:\n  {e}")

        recipient = parsed.get("gpg_recipient", recipient)
        gpg_encrypt(new_content, CONFIG_PATH, recipient)
        print(f"ts: config saved → {CONFIG_PATH}")
    finally:
        with open(tmp, "w") as f:
            f.write("\x00" * 4096)
        os.unlink(tmp)


def cmd_init() -> None:
    if os.path.exists(CONFIG_PATH):
        sys.exit(
            f"ts: config already exists at {CONFIG_PATH}\n"
            "    Run: ts edit"
        )
    gpg_encrypt(_EMPTY_CONFIG, CONFIG_PATH, recipient=None)
    print(f"ts: created config at {CONFIG_PATH}")
    print("    Run: ts edit   to add tools and secrets.")


def cmd_install() -> None:
    """
    Write ts to ~/.local/bin and add shell hook functions to .bashrc/.zshrc.
    Shell hook functions shadow the real command name so the user just types
    'cortex' or 'claude' and the picker appears automatically.
    """
    bin_dir = os.path.expanduser("~/.local/bin")
    os.makedirs(bin_dir, exist_ok=True)

    dest = os.path.join(bin_dir, "ts")
    if os.path.realpath(dest) != SELF:
        shutil.copy2(SELF, dest)
        os.chmod(dest, stat.S_IRWXU)
        print(f"ts: installed → {dest}")
    else:
        print(f"ts: already at {dest}")

    # Load config to know which tools to hook.
    if not os.path.exists(CONFIG_PATH):
        print("ts: no config yet — run 'ts init' first, then 'ts install' again.")
        return

    config = load_config()
    tools = list(config.get("tools", {}).keys())
    if not tools:
        print("ts: no tools in config, nothing to hook.")
        return

    # Build the shell snippet.
    lines = [
        "",
        "# --- toolstart hooks (added by: ts install) ---",
    ]
    for tool in tools:
        lines += [
            f'{tool}() {{',
            f'  command ts hook {tool} "$@"',
            f'}}',
        ]
    lines.append("# --- end toolstart hooks ---")
    snippet = "\n".join(lines) + "\n"

    shell = os.path.basename(os.environ.get("SHELL", "bash"))
    rc_candidates = {
        "zsh":  os.path.expanduser("~/.zshrc"),
        "bash": os.path.expanduser("~/.bashrc"),
    }
    rc_file = rc_candidates.get(shell, os.path.expanduser("~/.bashrc"))

    marker = "# --- toolstart hooks"

    if os.path.exists(rc_file):
        with open(rc_file) as f:
            content = f.read()
        if marker in content:
            import re
            content = re.sub(
                r"\n# --- toolstart hooks.*?# --- end toolstart hooks ---\n",
                "",
                content,
                flags=re.DOTALL,
            )
        with open(rc_file, "w") as f:
            f.write(content)

    with open(rc_file, "a") as f:
        f.write(snippet)

    print(f"ts: hooks for {tools} written to {rc_file}")
    print(f"    Reload with:  source {rc_file}")


# ---------------------------------------------------------------------------
# Empty config template
# ---------------------------------------------------------------------------

_EMPTY_CONFIG = """\
# toolstart config — edit with: ts edit
#
# Uncomment to use GPG public-key encryption instead of symmetric passphrase:
# gpg_recipient: you@example.com

tools:
  cortex:
    profiles:
      nonprod:
        env:
          SF_NP: your-nonprod-pat
          SF_P:  your-prod-pat
        cmd: [cortex, -c, nonprod]
      prod:
        env:
          SF_NP: your-nonprod-pat
          SF_P:  your-prod-pat
        cmd: [cortex, -c, prod]

  claude:
    profiles:
      default:
        env:
          ANTHROPIC_API_KEY: sk-ant-...
        cmd: [claude]
"""

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    subcmd = args[0]

    if subcmd == "init":
        cmd_init()
    elif subcmd == "edit":
        cmd_edit()
    elif subcmd == "list":
        cmd_list()
    elif subcmd == "install":
        cmd_install()
    elif subcmd == "hook":
        if len(args) < 2:
            sys.exit("ts: usage: ts hook <tool> [args...]")
        cmd_hook(args[1], args[2:])
    else:
        sys.exit(f"ts: unknown command '{subcmd}'.  Run: ts --help")


if __name__ == "__main__":
    main()
```

## Architecture

```
user types: cortex -p "explain this"
        |
        v
shell function in .zshrc:
  cortex() { command ts hook cortex "$@" }
        |
        v
ts hook cortex "-p" "explain this"
  1. gpg --decrypt ~/.config/toolstart/config.yaml.gpg
  2. parse YAML, find tool "cortex", list profiles ["nonprod", "prod"]
  3. curses picker shown to user -> user picks "nonprod"
  4. merge profile env vars into os.environ copy
  5. os.execvpe("cortex", ["cortex", "-c", "nonprod", "-p", "explain this"], env)
     ^^ replaces the ts process entirely, secrets only in memory during step 1-4
```

## Config schema

```yaml
# optional — omit for symmetric passphrase encryption
gpg_recipient: you@example.com

tools:
  <tool-name>:            # must match the real binary name (cortex, claude, aws...)
    profiles:
      <profile-name>:
        env:
          KEY: value      # injected into the process env
        cmd: [binary, arg1, arg2]   # base command; user args appended after
```

## Setup sequence (for the user)

```bash
pip install pyyaml
cp ts ~/.local/bin/ts && chmod 700 ~/.local/bin/ts
ts init           # creates ~/.config/toolstart/config.yaml.gpg (prompts for passphrase)
ts edit           # decrypt -> $EDITOR -> re-encrypt; add real secrets here
ts install        # copies ts to ~/.local/bin, writes shell functions to .zshrc/.bashrc
source ~/.zshrc
cortex            # picker appears
```

## Known issues / things to verify on a real machine

- **`ts install` idempotency**: strips old hook block via regex before re-writing. Needs testing when tool list changes between installs.
- **curses on macOS**: `curses.wrapper` should work fine but `use_default_colors()` behavior can vary by terminal. If colors look wrong, remove the `curses.init_pair` call and drop `curses.color_pair(1)`.
- **Single-profile auto-skip**: if a tool has exactly one profile, `pick_profile` returns it immediately without showing the picker. This is intentional.
- **`cmd_edit` temp file wipe**: zeroes 4096 bytes then unlinks. Does not account for files larger than 4096 bytes if the config is very large.
- **`ts install` shell detection**: reads `$SHELL` env var. If the user's login shell differs from their active shell, the wrong rc file could be targeted.
- **Python version**: uses `str | None` union syntax which requires Python 3.10+. Should add a version guard at the top or rewrite as `Optional[str]` for broader compat.

## Potential improvements

- `ts add <tool>` interactive wizard to add a new tool without opening the raw YAML
- `ts rotate <tool> <profile>` to update a single secret value
- Support `age` encryption as an alternative to GPG (simpler UX)
- Numbered key shortcuts in the picker (press `1`, `2` to select by index)
- `TS_SKIP_PICKER=nonprod cortex` env var to bypass the picker in scripts
