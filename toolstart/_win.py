"""Windows: msvcrt key reads, child-process launch, PowerShell $PROFILE hooks."""

import ctypes
import msvcrt
import shutil
import subprocess
import sys

RELOAD = ". '{}'"

# Let the console interpret the menu's ANSI escapes (Windows Terminal does already, old conhost doesn't).
_k32, _mode = ctypes.windll.kernel32, ctypes.c_uint32()
_stderr = _k32.GetStdHandle(-12)  # STD_ERROR_HANDLE
if _k32.GetConsoleMode(_stderr, ctypes.byref(_mode)):
    _k32.SetConsoleMode(_stderr, _mode.value | 0x4)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def raise_window(tk_root) -> None:
    """Windows only lets the foreground app hand out the foreground, and that right is lost along
    PowerShell → toolstart.exe → python, so Tk's focus_force() is ignored. Briefly join the
    foreground window's input queue, which makes SetForegroundWindow allowed, then detach."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(tk_root.winfo_id())  # winfo_id is Tk's inner frame; its parent is the window
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    me = _k32.GetCurrentThreadId()
    user32.AttachThreadInput(me, fg_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.AttachThreadInput(me, fg_thread, False)


def read_key() -> str:
    """One keypress. Arrow keys come as two chars: '\\xe0' (or '\\x00') + 'H'/'P'."""
    k = msvcrt.getwch()
    return k + msvcrt.getwch() if k in ("\x00", "\xe0") else k


def launch(cmd: list[str] | str, args: tuple[str, ...], env: dict) -> None:
    """Windows has no real exec: run the tool as a child and pass its exit code through."""
    if isinstance(cmd, str):  # shell string: run via cmd.exe, user args quoted and appended
        argv, shell = f"{cmd} {subprocess.list2cmdline(args)}", True
    else:  # which: resolve .cmd/.bat shims (npm tools, VS Code) that CreateProcess won't find
        argv, shell = [shutil.which(cmd[0], path=env.get("PATH")) or cmd[0], *cmd[1:], *args], False
    try:
        proc = subprocess.Popen(argv, env=env, shell=shell)
    except FileNotFoundError:
        sys.exit(f"toolstart: command not found: {cmd[0]}")
    while True:  # Ctrl-C reaches the whole console: the child handles it, we keep waiting
        try:
            sys.exit(proc.wait())
        except KeyboardInterrupt:
            pass


def hook_files(tools: list[str]) -> dict[str, str]:
    """{$PROFILE path: hook functions} for each installed PowerShell (pwsh 7, Windows PowerShell 5.1)."""
    hooks = "".join(f"function {t} {{ toolstart hook {t} @args }}\n" for t in tools)
    profiles = (
        subprocess.run([exe, "-NoProfile", "-Command", "$PROFILE"], capture_output=True, text=True).stdout.strip()
        for exe in ("pwsh", "powershell") if shutil.which(exe)
    )
    return {p: hooks for p in profiles if p}
