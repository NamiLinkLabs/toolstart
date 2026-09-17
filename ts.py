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
        # A shell function shadows the real binary.
        # It calls 'ts hook <tool> "$@"' which execs the real binary on success.
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

    # Remove any previous block so we don't accumulate duplicates.
    if os.path.exists(rc_file):
        with open(rc_file) as f:
            content = f.read()
        if marker in content:
            # Strip from first marker line to end-marker line (inclusive).
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
          SF_P:  your-prod-pat          # used by connections.toml ${SF_P}
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
