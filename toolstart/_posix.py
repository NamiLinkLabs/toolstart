"""macOS / Linux: raw-tty key reads, exec launch, zsh/bash hooks."""

import os
import shlex
import sys
import termios
import tty

RELOAD = "source {}"


def read_key() -> str:
    """One keypress (arrow keys arrive as a single escape sequence). Reads /dev/tty so piped stdin still works."""
    fd = os.open("/dev/tty", os.O_RDONLY)
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return os.read(fd, 8).decode(errors="ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        os.close(fd)


def launch(cmd: list[str] | str, args: tuple[str, ...], env: dict) -> None:
    """exec the tool: replaces this process, secrets only live in the tool's own env."""
    if isinstance(cmd, str):  # shell string: run via sh -c, user args quoted and appended
        argv = ["sh", "-c", f"{cmd} {shlex.join(args)}"]
    else:
        argv = [*cmd, *args]
    try:
        os.execvpe(argv[0], argv, env)
    except FileNotFoundError:
        sys.exit(f"toolstart: command not found: {argv[0]}")


def hook_files(tools: list[str]) -> dict[str, str]:
    """{rc file: hook functions}. The rc file follows $SHELL."""
    shell = os.path.basename(os.environ.get("SHELL", "bash"))
    rc = os.path.expanduser("~/.zshrc" if shell == "zsh" else "~/.bashrc")
    return {rc: "".join(f'{t}() {{ command toolstart hook {t} "$@"; }}\n' for t in tools)}
