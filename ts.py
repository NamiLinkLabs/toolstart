#!/usr/bin/env python3
"""
ts — toolstart
==============
GPG-encrypted secret injector with interactive profile picker.

Commands:
  ts hook <tool> [args...]           Called by shell hooks — shows profile picker,
                                     injects secrets, execs the tool.
  ts get <tool> <profile> <key>      Print a single env-var value (for subshell use).
  ts edit                            Decrypt config in your editor and re-encrypt.
  ts list                            List configured tools and profiles.
  ts install                         Install shell hooks into your rc file.
  ts init                            Create an empty encrypted config.

Config: ~/.config/toolstart/config.yaml.gpg  (override: $TS_CONFIG)
Config format: see the template written by `ts init`, or README.md.
"""

import os
import re
import sys
import subprocess
import tempfile
import shutil
import shlex
import curses

try:
    import yaml
except ImportError:
    sys.exit("ts: requires PyYAML — run: pip install pyyaml")

SELF = os.path.realpath(__file__)
CONFIG_PATH = os.environ.get("TS_CONFIG", os.path.expanduser("~/.config/toolstart/config.yaml.gpg"))
DEFAULT_EDITOR = os.environ.get("EDITOR", "code --wait")

EMPTY_CONFIG = """\
# toolstart config — edit with: ts edit
#
# Editor command used by `ts edit` (change it here any time):
editor: {editor}
#
# Uncomment to use GPG public-key encryption instead of symmetric passphrase:
# gpg_recipient: you@example.com

# Optional: env vars applied to every tool/profile (profile env overrides):
# env:
#   SOME_GLOBAL_VAR: value

tools:
  cortex:
    profiles:
      np:
        env:
          SNOWFLAKE_CONNECTIONS_NP_PASSWORD: your-nonprod-pat
        cmd: "cortex -c np"
      p:
        env:
          SNOWFLAKE_CONNECTIONS_P_PASSWORD:  your-prod-pat
        cmd: "cortex -c p"

  claude:
    profiles:
      default:
        env:
          ANTHROPIC_API_KEY: sk-ant-...
        cmd: claude

  pypi:
    profiles:
      default:
        env:
          UV_PUBLISH_TOKEN: pypi-...
        cmd: "uv publish"
"""

# ---------------------------------------------------------------------------
# GPG + config
# ---------------------------------------------------------------------------

def gpg(args: list[str], what: str, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(["gpg", "--quiet", "--yes", *args], stderr=subprocess.PIPE, **kw)
    if r.returncode:
        sys.exit(f"ts: GPG {what} failed:\n  {r.stderr.decode(errors='replace').strip()}")
    return r


def gpg_decrypt(path: str) -> str:
    return gpg(["--batch", "--decrypt", path], "decryption", stdout=subprocess.PIPE).stdout.decode()


def wipe(path: str) -> None:
    """Overwrite a temp file with zeros, then delete it."""
    size = os.path.getsize(path)
    with open(path, "wb") as f:
        f.write(b"\0" * size)
    os.unlink(path)


def gpg_encrypt(plaintext: str, path: str, recipient: str | None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Plaintext goes via a temp file (mkstemp creates it 0600) so the terminal
    # stays free for GPG's passphrase prompt.
    fd, tmp = tempfile.mkstemp(prefix="ts-plain-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(plaintext)
        if recipient:
            gpg(["--batch", "--encrypt", "--recipient", recipient, "--output", path, tmp], "encryption")
        else:  # no --batch: GPG prompts for the passphrase
            gpg(["--symmetric", "--cipher-algo", "AES256", "--output", path, tmp], "encryption")
    finally:
        wipe(tmp)
    os.chmod(path, 0o600)


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"ts: no config found at {CONFIG_PATH}\n    Run: ts init")
    try:
        return yaml.safe_load(gpg_decrypt(CONFIG_PATH)) or {}
    except yaml.YAMLError as e:
        sys.exit(f"ts: invalid YAML in config: {e}")


def editor_of(text: str) -> list[str]:
    """Editor command from the config's `editor:` key; falls back to $EDITOR, then VS Code."""
    try:
        cfg = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        cfg = {}
    return shlex.split(str(cfg.get("editor") or DEFAULT_EDITOR))


def get_tool(config: dict, name: str) -> dict:
    tools = config.get("tools", {})
    if name not in tools:
        sys.exit(f"ts: unknown tool '{name}'.  Configured: {', '.join(tools) or '(none)'}")
    return tools[name]


# ---------------------------------------------------------------------------
# Interactive profile picker (curses)
# ---------------------------------------------------------------------------

def pick_profile(tool_name: str, profiles: list[str]) -> str | None:
    """Arrow-key selector. Returns the chosen name, or None on q/Escape/Ctrl-C."""
    if len(profiles) == 1:
        return profiles[0]

    def _picker(stdscr):
        curses.curs_set(0)
        idx = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            stdscr.addstr(0, 0, f" ts › {tool_name} — select profile (↑↓ enter, q=quit) "[:w], curses.A_BOLD)
            for i, name in enumerate(profiles[: max(h - 2, 0)]):
                label = f"  {'›' if i == idx else ' '} {name}"
                stdscr.addstr(i + 2, 0, label[:w], curses.A_REVERSE if i == idx else 0)
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(profiles)
            elif key in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(profiles)
            elif key in (curses.KEY_ENTER, 10, 13):
                return profiles[idx]
            elif key in (ord("q"), ord("Q"), 27):  # 27 = Escape
                return None

    try:
        return curses.wrapper(_picker)
    except KeyboardInterrupt:
        return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_hook(tool_name: str, *extra_args: str) -> None:
    config = load_config()
    profiles = get_tool(config, tool_name).get("profiles") or {}
    if not profiles:
        sys.exit(f"ts: tool '{tool_name}' has no profiles defined.")

    chosen = pick_profile(tool_name, list(profiles))
    if chosen is None:
        sys.exit(0)  # cancelled — return to the shell prompt quietly

    profile = profiles[chosen]
    # Global env (top-level `env:`) applies to every profile; profile env overrides.
    env = os.environ \
        | {str(k): str(v) for k, v in (config.get("env") or {}).items()} \
        | {str(k): str(v) for k, v in (profile.get("env") or {}).items()}

    cmd = profile.get("cmd")
    if not cmd:
        sys.exit(f"ts: profile '{chosen}' in tool '{tool_name}' has no 'cmd'.")
    if isinstance(cmd, str):  # shell string: run via sh -c, user args quoted and appended
        argv = ["sh", "-c", f"{cmd} {shlex.join(extra_args)}"]
    else:
        argv = [*cmd, *extra_args]
    try:
        os.execvpe(argv[0], argv, env)
    except FileNotFoundError:
        sys.exit(f"ts: command not found: {argv[0]}")


def cmd_get(tool_name: str, profile_name: str, key: str) -> None:
    """Print one env-var value to stdout, no newline — for $(subshell) capture."""
    config = load_config()
    profiles = get_tool(config, tool_name).get("profiles") or {}
    if profile_name not in profiles:
        sys.exit(f"ts: unknown profile '{profile_name}' for '{tool_name}'.  Available: {', '.join(profiles) or '(none)'}")
    env = {**(config.get("env") or {}), **(profiles[profile_name].get("env") or {})}
    if key not in env:
        sys.exit(f"ts: key '{key}' not found in '{tool_name}/{profile_name}'.  Available: {', '.join(env) or '(none)'}")
    print(env[key], end="")


def cmd_list() -> None:
    config = load_config()
    tools = config.get("tools", {})
    if not tools:
        print("ts: no tools configured.")
    if global_env := ", ".join(config.get("env") or {}):
        print(f"  (global env: {global_env})")
    for tname, tval in tools.items():
        print(f"  {tname}")
        for pname, pval in (tval.get("profiles") or {}).items():
            cmd = pval.get("cmd") or []
            print(f"    {pname:<14}  cmd: {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
            if env_keys := ", ".join(pval.get("env") or {}):
                print(f"    {'':14}  env: {env_keys}")


def cmd_edit() -> None:
    old = gpg_decrypt(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else EMPTY_CONFIG.format(editor=DEFAULT_EDITOR)

    fd, tmp = tempfile.mkstemp(suffix=".yaml", prefix="toolstart-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(old)
        subprocess.run(editor_of(old) + [tmp])
        with open(tmp) as f:
            new = f.read()
    finally:
        wipe(tmp)

    if new == old:
        print("ts: no changes, config unchanged.")
        return
    try:
        parsed = yaml.safe_load(new) or {}
    except yaml.YAMLError as e:
        sys.exit(f"ts: invalid YAML — config NOT saved:\n  {e}")
    gpg_encrypt(new, CONFIG_PATH, parsed.get("gpg_recipient"))
    print(f"ts: config saved → {CONFIG_PATH}")
    cmd_install()


def cmd_init() -> None:
    if os.path.exists(CONFIG_PATH):
        sys.exit(f"ts: config already exists at {CONFIG_PATH}\n    Run: ts edit")
    editor = input(f"Editor for 'ts edit' [{DEFAULT_EDITOR}]: ").strip() or DEFAULT_EDITOR
    gpg_encrypt(EMPTY_CONFIG.format(editor=editor), CONFIG_PATH, recipient=None)
    print(f"ts: created config at {CONFIG_PATH}\n    Run: ts edit   to add tools and secrets.")


def cmd_install() -> None:
    """Copy ts to ~/.local/bin and write one shadowing shell function per tool to the rc file."""
    dest = os.path.expanduser("~/.local/bin/ts")
    if os.path.realpath(dest) != SELF:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(SELF, dest)
        os.chmod(dest, 0o700)
        print(f"ts: installed → {dest}")
    else:
        print(f"ts: already at {dest}")

    if not os.path.exists(CONFIG_PATH):
        print("ts: no config yet — run 'ts init' first, then 'ts install' again.")
        return
    tools = list(load_config().get("tools", {}))
    if not tools:
        print("ts: no tools in config, nothing to hook.")
        return

    hooks = "".join(f'{t}() {{ command ts hook {t} "$@"; }}\n' for t in tools)
    snippet = f"\n# --- toolstart hooks (added by: ts install) ---\n{hooks}# --- end toolstart hooks ---\n"

    shell = os.path.basename(os.environ.get("SHELL", "bash"))
    rc_file = os.path.expanduser("~/.zshrc" if shell == "zsh" else "~/.bashrc")
    content = open(rc_file).read() if os.path.exists(rc_file) else ""
    # Drop any previous block so re-runs don't accumulate duplicates.
    content = re.sub(r"\n# --- toolstart hooks.*?# --- end toolstart hooks ---\n", "", content, flags=re.S)
    with open(rc_file, "w") as f:
        f.write(content + snippet)

    print(f"ts: hooks for {tools} written to {rc_file}\n    Reload with:  source {rc_file}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    sub, rest = args[0], args[1:]
    simple = {"init": cmd_init, "edit": cmd_edit, "list": cmd_list, "install": cmd_install}
    if sub in simple:
        simple[sub]()
    elif sub == "get" and len(rest) == 3:
        cmd_get(*rest)
    elif sub == "hook" and rest:
        cmd_hook(*rest)
    elif sub in ("get", "hook"):
        sys.exit("ts: usage: ts get <tool> <profile> <key>" if sub == "get" else "ts: usage: ts hook <tool> [args...]")
    else:
        sys.exit(f"ts: unknown command '{sub}'.  Run: ts --help")


if __name__ == "__main__":
    main()
