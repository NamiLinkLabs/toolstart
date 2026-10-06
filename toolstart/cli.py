"""
toolstart
=========
GPG-encrypted secret injector with interactive profile picker.

Commands:
  toolstart hook <tool> [args...]         Called by shell hooks — shows profile picker,
                                          injects secrets, launches the tool.
  toolstart get <tool> <profile> <key>    Print a single env-var value (for subshell use).
  toolstart edit                          Edit tools, profiles and secrets in a window.
  toolstart list                          List configured tools and profiles.
  toolstart install                       Write shell hooks (zsh/bash rc file, PowerShell $PROFILE).
  toolstart init                          Pick a GPG key and create an empty encrypted config.

Config: ~/.config/toolstart/config.yaml.gpg  (override: $TS_CONFIG)
"""

import getpass
import os
import re
import sys
import subprocess
import shutil

import yaml

if sys.platform == "win32":
    from toolstart import _win as plat
else:
    from toolstart import _posix as plat

CONFIG_PATH = os.environ.get("TS_CONFIG", os.path.expanduser("~/.config/toolstart/config.yaml.gpg"))
HOOKS_BEGIN, HOOKS_END = "# --- toolstart hooks (added by: toolstart install) ---", "# --- end toolstart hooks ---"
HAND_FIX = "\n    The config was edited by hand — fix it the same way (see README › Troubleshooting)."

# ---------------------------------------------------------------------------
# GPG + config
# ---------------------------------------------------------------------------

def gpg(args: list[str], what: str, check=True, **kw) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(["gpg", "--quiet", "--yes", *args], stderr=subprocess.PIPE, **kw)
    except FileNotFoundError:
        sys.exit("toolstart: gpg not found on PATH — install GnuPG first (see README).")
    if check and r.returncode:
        sys.exit(f"toolstart: GPG {what} failed:\n  {r.stderr.decode(errors='replace').strip()}")
    return r


def ask_terminal(prompt: str) -> str | None:
    """Passphrase prompt in the terminal, no echo. None = cancelled."""
    try:
        return getpass.getpass(f"toolstart: {prompt} ")
    except (EOFError, KeyboardInterrupt):
        return None


def gpg_pass(args: list[str], what: str, ask, data: bytes = b"", passphrase: str | None = None) -> bytes:
    """gpg with toolstart handling the passphrase instead of a pinentry popup (on Windows those
    open behind the terminal / the editor window). Loopback mode: first try without one —
    gpg-agent may have it cached, or the key has none — and only if gpg asks for it, ask()."""
    for attempt in range(4):
        fd = [] if passphrase is None else ["--passphrase-fd", "0"]  # first stdin line; data follows
        stdin = b"" if passphrase is None else passphrase.encode() + b"\n"
        r = gpg(["--batch", "--pinentry-mode", "loopback", *fd, *args], what, check=False,
                input=stdin + data, stdout=subprocess.PIPE)
        # needs one / key passphrase wrong / symmetric passphrase wrong
        wants = any(m in r.stderr for m in (b"can't get input", b"Bad passphrase", b"Bad session key"))
        if r.returncode == 0 or not wants or attempt == 3:
            break
        passphrase = ask("Wrong passphrase, try again:" if passphrase is not None else "GPG passphrase:")
        if passphrase is None:
            sys.exit("toolstart: cancelled.")
    if r.returncode:
        sys.exit(f"toolstart: GPG {what} failed:\n  {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def new_passphrase(ask, what: str) -> str:
    first = ask(f"New passphrase {what}:")
    if first is None or first != ask("Repeat it:"):
        sys.exit("toolstart: the passphrases don't match (or input was cancelled) — nothing changed.")
    return first


def gpg_keys() -> list[tuple[str, str]]:
    """(fingerprint, first user id) of each usable secret key, from gpg's machine-readable listing."""
    out = gpg(["--list-secret-keys", "--with-colons"], "key listing", stdout=subprocess.PIPE).stdout
    keys, fpr = [], ""
    for f in (line.split(":") for line in out.decode(errors="replace").splitlines()):
        if f[0] == "sec":  # new key; skip expired (e) / revoked (r) ones
            fpr = None if f[1] not in ("e", "r") else ""
        elif f[0] == "fpr" and fpr is None:  # first fpr after sec = primary key's
            fpr = f[9]
        elif f[0] == "uid" and fpr:
            keys.append((fpr, f[9]))
            fpr = ""  # only the first uid; ignore subkeys' fpr lines
    return keys


def load_config(ask=ask_terminal) -> dict:
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"toolstart: no config found at {CONFIG_PATH}\n    Run: toolstart init")
    text = gpg_pass(["--decrypt", CONFIG_PATH], "decryption", ask).decode()
    try:
        return check_config(yaml.safe_load(text) or {})
    except yaml.YAMLError as e:
        # Position + problem only — str(e) would quote the offending line, which may hold a secret.
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        sys.exit(f"toolstart: invalid YAML in config{where}: {getattr(e, 'problem', None) or type(e).__name__}{HAND_FIX}")


def write_config(config: dict, ask=ask_terminal) -> None:
    """Encrypt to CONFIG_PATH. The plaintext only travels through a pipe to gpg — it never touches disk.
    gpg writes a sibling file that replaces the config only on success (a cancelled passphrase can't truncate it)."""
    text = "# toolstart config — managed by `toolstart edit`\n" + yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    if recipient := config.get("gpg_recipient"):
        mode, passphrase = ["--encrypt", "--recipient", recipient], None
    else:
        mode, passphrase = ["--symmetric", "--cipher-algo", "AES256"], new_passphrase(ask, "for the config")
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".new"
    gpg_pass([*mode, "--output", tmp], "encryption", ask, data=text.encode(), passphrase=passphrase)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_PATH)


def mapping(value, where: str) -> dict:
    """`value` as a dict, or exit naming `where`. The value is never printed: it may be a secret."""
    if value is None or isinstance(value, dict):
        return value or {}
    hint = ""
    if isinstance(value, str) and (m := re.match(r"\s*([\w.-]+):\S", value)):  # `KEY:value` parses as one string
        hint = f"\n    Missing space after '{m[1]}:'? Write it as `{m[1]}: value`."
    sys.exit(f"toolstart: {where} must be indented `name: value` lines, got a {type(value).__name__}.{hint}{HAND_FIX}")


def check_config(config) -> dict:
    """Validate the shape of a (possibly hand-written) config, so mistakes get a pointed message
    instead of a traceback later. Each level is written back normalized ({} for empty)."""
    config = mapping(config, "the config")
    config.pop("editor", None)  # leftover from the YAML-editor days
    config["env"] = mapping(config.get("env"), "top-level `env:`")
    tools = config["tools"] = mapping(config.get("tools"), "`tools:`")
    for t in tools:
        tool = tools[t] = mapping(tools[t], f"tool '{t}'")
        profiles = tool["profiles"] = mapping(tool.get("profiles"), f"'{t}' `profiles:`")
        for p in profiles:
            prof = profiles[p] = mapping(profiles[p], f"profile '{t}/{p}'")
            prof["env"] = mapping(prof.get("env"), f"'{t}/{p}' `env:`")
            if not isinstance(prof.get("cmd"), (str, list, type(None))):
                sys.exit(f"toolstart: '{t}/{p}' `cmd:` must be a string or a list.{HAND_FIX}")
    return config


def profiles_of(config: dict, tool: str) -> dict:
    tools = config["tools"]
    if tool not in tools:
        sys.exit(f"toolstart: unknown tool '{tool}'.  Configured: {', '.join(tools) or '(none)'}")
    if not (profiles := tools[tool]["profiles"]):
        sys.exit(f"toolstart: tool '{tool}' has no profiles defined.")
    return profiles


# ---------------------------------------------------------------------------
# Interactive menu (ANSI escapes + raw key reads — no curses)
# ---------------------------------------------------------------------------

KEYS = {
    "\x1b[A": "up", "\x1bOA": "up", "\xe0H": "up", "\x00H": "up", "k": "up",            # POSIX / Windows arrows
    "\x1b[B": "down", "\x1bOB": "down", "\xe0P": "down", "\x00P": "down", "j": "down",
    "\r": "enter", "\n": "enter",
    "q": "quit", "Q": "quit", "\x1b": "quit", "\x03": "quit",                           # \x03 = Ctrl-C in raw mode
}


def choose(title: str, items: list[str]) -> int | None:
    """Inline arrow-key menu drawn on stderr and erased afterwards.
    Returns the chosen index, or None on q/Escape/Ctrl-C."""
    out, idx, drawn = sys.stderr, 0, 0
    width = shutil.get_terminal_size().columns - 1  # never wrap, or cursor-up miscounts
    try:
        out.write("\x1b[?25l")  # hide cursor
        while True:
            lines = [f"\x1b[1m{title[:width]}\x1b[0m"] + [
                f"\x1b[7m{('  › ' + item)[:width]}\x1b[0m" if i == idx else ("    " + item)[:width]
                for i, item in enumerate(items)
            ]
            # \x1b[nF: back to the menu's first line, \x1b[J: clear to end of screen
            out.write((f"\x1b[{drawn}F" if drawn else "") + "\x1b[J" + "\n".join(lines) + "\n")
            out.flush()
            drawn = len(lines)
            key = KEYS.get(plat.read_key())
            if key in ("up", "down"):
                idx = (idx + (1 if key == "down" else -1)) % len(items)
            elif key == "enter":
                return idx
            elif key == "quit":
                return None
    except KeyboardInterrupt:
        return None
    finally:
        out.write((f"\x1b[{drawn}F\x1b[J" if drawn else "") + "\x1b[?25h")
        out.flush()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def env_of(config: dict, profile: dict) -> dict:
    """Global env (top-level `env:`) + profile env (overrides)."""
    return config["env"] | profile["env"]


def resolve_env(config: dict, profile: dict, keys=None) -> dict[str, str]:
    """env_of() with each `$(cmd)` replaced by the command's stdout (only for `keys`, if given).
    Plain `$VAR` is left alone so secrets containing `$` survive."""
    env = {str(k): str(v) for k, v in env_of(config, profile).items()}

    def run(m: re.Match) -> str:
        # shell=True: /bin/sh on POSIX, cmd.exe on Windows. stdin/stderr inherited so
        # interactive helpers (MFA prompts etc.) still work.
        r = subprocess.run(m.group(1), shell=True, stdout=subprocess.PIPE, env=os.environ | env)
        if r.returncode:
            sys.exit(f"toolstart: command failed (exit {r.returncode}): {m.group(1)}")
        return r.stdout.decode().rstrip("\r\n")

    return {k: re.sub(r"\$\(([^()]+)\)", run, v) for k, v in env.items() if keys is None or k in keys}


def cmd_hook(tool: str, *args: str) -> None:
    config = load_config()
    profiles = profiles_of(config, tool)
    if config.get("update_check", True):
        from toolstart import update  # not even imported when disabled
        update.offer(restart=lambda: plat.launch([sys.executable, "-m", "toolstart", "hook", tool], args, os.environ))

    names = list(profiles)
    i = 0 if len(names) == 1 else choose(f"toolstart › {tool} — select profile (↑↓ enter, q=quit)", names)
    if i is None:
        sys.exit(0)  # cancelled — return to the shell prompt quietly
    profile = profiles[names[i]]
    if not (cmd := profile.get("cmd")):
        sys.exit(f"toolstart: profile '{names[i]}' in tool '{tool}' has no 'cmd'.")
    plat.launch(cmd, args, os.environ | resolve_env(config, profile))


def cmd_get(tool: str, profile_name: str, key: str) -> None:
    """Print one env-var value to stdout, no newline — for $(subshell) capture."""
    config = load_config()
    profiles = profiles_of(config, tool)
    if profile_name not in profiles:
        sys.exit(f"toolstart: unknown profile '{profile_name}' for '{tool}'.  Available: {', '.join(profiles)}")
    raw = env_of(config, profiles[profile_name])
    if key not in raw:
        sys.exit(f"toolstart: key '{key}' not found in '{tool}/{profile_name}'.  Available: {', '.join(raw) or '(none)'}")
    # Resolve only the requested key so unrelated $(...) helpers don't run.
    print(resolve_env(config, profiles[profile_name], keys=[key])[key], end="")


def cmd_list() -> None:
    config = load_config()
    if not config["tools"]:
        print("toolstart: no tools configured.")
    if global_env := ", ".join(config["env"]):
        print(f"  (global env: {global_env})")
    for tname, tval in config["tools"].items():
        print(f"  {tname}")
        for pname, pval in tval["profiles"].items():
            cmd = pval.get("cmd") or []
            print(f"    {pname:<14}  cmd: {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
            if env_keys := ", ".join(pval["env"]):
                print(f"    {'':14}  env: {env_keys}")


def save_config(config: dict, ask=ask_terminal) -> None:
    write_config(config, ask)
    print(f"toolstart: config saved → {CONFIG_PATH}")
    cmd_install(list(config["tools"]))


def cmd_edit() -> None:
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"toolstart: no config found at {CONFIG_PATH}\n    Run: toolstart init")
    try:
        import tkinter
        from toolstart import gui
        editor = gui.Editor(load=lambda ask: (load_config(ask), gpg_keys()), on_save=save_config)
    except ImportError:
        sys.exit("toolstart: `toolstart edit` needs tkinter, which this Python lacks.\n"
                 "    macOS Homebrew: brew install python-tk   Debian/Ubuntu: sudo apt install python3-tk\n"
                 "    or reinstall on uv's own Python: uv tool install --python-preference only-managed --force toolstart")
    except tkinter.TclError as e:
        sys.exit(f"toolstart: can't open a window ({e}) — `toolstart edit` needs a desktop session.")
    editor.run()


def cmd_init() -> None:
    if os.path.exists(CONFIG_PATH):
        sys.exit(f"toolstart: config already exists at {CONFIG_PATH}\n    Run: toolstart edit")
    keys = gpg_keys()
    items = [f"{uid}  ({fpr[-16:]})" for fpr, uid in keys]
    items += ["create a new key", "no key — symmetric passphrase (asked twice on every save)"]
    i = choose("Encrypt the config to which GPG key? (↑↓ enter, q=quit)", items)
    if i is None:
        sys.exit(0)
    config = {}
    if i < len(keys):
        config["gpg_recipient"] = keys[i][0]
    elif i == len(keys):
        uid = input("Name and email for the new key, e.g. Jane Doe <jane@example.com>: ").strip()
        if not uid:
            sys.exit("toolstart: no name/email given, nothing created.")
        passphrase = new_passphrase(ask_terminal, "for the new key (empty = none)")
        gpg_pass(["--quick-gen-key", uid, "default", "default", "never"], "key generation", ask_terminal,
                 passphrase=passphrase)
        config["gpg_recipient"] = uid
    write_config(config | {"env": {}, "tools": {}})
    print(f"toolstart: created config at {CONFIG_PATH}\n    Run: toolstart edit   to add tools and secrets.")


def cmd_install(tools: list[str] | None = None) -> None:
    """Write one shadowing shell function per configured tool into the shell's rc/profile file(s)."""
    if tools is None:
        if not os.path.exists(CONFIG_PATH):
            print("toolstart: no config yet — run 'toolstart init' first, then 'toolstart install' again.")
            return
        tools = list(load_config()["tools"])
    if not tools:
        print("toolstart: no tools in config, nothing to hook.")
        return
    if not (files := plat.hook_files(tools)):
        sys.exit("toolstart: no supported shell found to install hooks into.")
    for path, hooks in files.items():
        content = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        # Drop any previous block so re-runs don't accumulate duplicates.
        content = re.sub(rf"\n{re.escape(HOOKS_BEGIN)}.*?{re.escape(HOOKS_END)}\n", "", content, flags=re.S)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{content}\n{HOOKS_BEGIN}\n{hooks}{HOOKS_END}\n")
        print(f"toolstart: hooks for {tools} written to {path}\n    Reload with:  {plat.RELOAD.format(path)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Expected problems exit with their own message; anything else gets one line, not a traceback."""
    try:
        dispatch(sys.argv[1:])
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        if os.environ.get("TS_DEBUG"):
            raise
        sys.exit(f"toolstart: unexpected error — {type(e).__name__}: {e}\n    Details: set TS_DEBUG=1 and rerun.")


def dispatch(args: list[str]) -> None:
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
        sys.exit("toolstart: usage: toolstart get <tool> <profile> <key>" if sub == "get" else "toolstart: usage: toolstart hook <tool> [args...]")
    else:
        sys.exit(f"toolstart: unknown command '{sub}'.  Run: toolstart --help")
