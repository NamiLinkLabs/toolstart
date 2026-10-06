"""Drive the picker + update modal through a pseudo-terminal (POSIX). Run: python tests/test_picker.py
Needs `toolstart` importable (installed package for the update tests) and gpg."""

import json
import os
import pty
import select
import subprocess
import sys
import tempfile
import time

T = tempfile.mkdtemp()
env = os.environ | {"HOME": T, "GNUPGHOME": f"{T}/gnupg", "TS_CONFIG": f"{T}/cfg.gpg", "TERM": "xterm"}
os.makedirs(env["GNUPGHOME"], mode=0o700)
gpg = ["gpg", "--batch", "--quiet", "--yes"]
subprocess.run([*gpg, "--passphrase", "", "--quick-gen-key", "ts@test", "default", "default", "never"], env=env, check=True)
CONFIG = """gpg_recipient: ts@test
tools:
  multi: {profiles: {a: {cmd: "echo PICKED-a"}, b: {cmd: "echo PICKED-b"}}}
  single: {profiles: {only: {cmd: "echo PICKED-only"}}}
"""
subprocess.run([*gpg, "--encrypt", "-r", "ts@test", "-o", env["TS_CONFIG"]], input=CONFIG.encode(), env=env, check=True)
CACHE = f"{T}/.cache/toolstart/update-check.json"
UP, DOWN, ENTER, ESC = b"\x1b[A", b"\x1b[B", b"\r", b"\x1b"


def run(cmd: str, *keys: bytes, **env_over: str) -> str:
    """Run `toolstart <cmd>` on a pty, send one key whenever output goes quiet, return the full output."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, "-m", "toolstart", *cmd.split()], env | env_over)
    os.set_blocking(fd, False)  # macOS: select can flag a pty readable and read still block
    out, keys, deadline = b"", list(keys), time.time() + 20
    # Poll until the child exits (gpg-agent inherits the pty, so EOF alone never comes).
    while time.time() < deadline:
        if select.select([fd], [], [], 0.8)[0]:
            try:
                out += os.read(fd, 4096)
            except OSError:
                pass
        elif keys:  # output went quiet: menu / prompt is waiting for input
            os.write(fd, keys.pop(0))
        if os.waitpid(pid, os.WNOHANG)[0]:
            break
    else:
        os.kill(pid, 9)
        sys.exit(f"FAIL timeout, output so far:\n{out.decode(errors='replace')}")
    while select.select([fd], [], [], 0.2)[0]:  # drain what's left
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    os.close(fd)
    return out.decode(errors="replace")


def cache(**kw) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump({"checked": time.time(), **kw}, f)


def decrypt(path: str, passphrase: str = "") -> str:
    return subprocess.run([*gpg, "--pinentry-mode", "loopback", "--passphrase", passphrase, "--decrypt", path],
                          capture_output=True, env=env, check=True).stdout.decode()


def check(name: str, ok: bool) -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


cache(latest="0.0.0")  # nothing newer
check("picker: down+enter picks 2nd", "PICKED-b" in run("hook multi", DOWN, ENTER))
check("picker: wraps around (up from top)", "PICKED-b" in run("hook multi", UP, ENTER))
check("picker: q cancels", "PICKED" not in run("hook multi", b"q"))
check("single profile: no menu", "PICKED-only" in run("hook single"))

cache(latest="999.0.0")
out = run("hook single", ESC)
check("update modal shown even for single profile", "999.0.0 is available" in out and "PICKED-only" in out)
check("esc = later (not skipped)", "skipped" not in json.load(open(CACHE)))
out = run("hook multi", DOWN, ENTER, ENTER)
check("skip this version, then picker", "PICKED-a" in out and json.load(open(CACHE))["skipped"] == "999.0.0")
check("skipped version not offered again", "available" not in run("hook single"))
cache(latest="999.0.1", skipped="999.0.0")
check("newer than skipped is offered", "999.0.1 is available" in run("hook single", ESC))

# A key with a passphrase: asked in the terminal (no pinentry popup), then cached by gpg-agent.
cache(latest="0.0.0")
subprocess.run([*gpg, "--pinentry-mode", "loopback", "--passphrase", "lockpw", "--quick-gen-key", "lock@test",
                "default", "default", "never"], env=env, check=True)
subprocess.run([*gpg, "--encrypt", "-r", "lock@test", "-o", f"{T}/locked.gpg"], input=CONFIG.encode(), env=env, check=True)
subprocess.run(["gpgconf", "--reload", "gpg-agent"], env=env, check=True)
out = run("hook single", b"nope\r", b"lockpw\r", TS_CONFIG=f"{T}/locked.gpg")
check("locked key: passphrase asked in the terminal", "toolstart: GPG passphrase:" in out)
check("wrong passphrase: asked again, then runs", "Wrong passphrase, try again:" in out and "PICKED-only" in out)
out = run("hook single", TS_CONFIG=f"{T}/locked.gpg")
check("then cached by gpg-agent: no prompt", "passphrase" not in out and "PICKED-only" in out)
subprocess.run(["gpgconf", "--reload", "gpg-agent"], env=env, check=True)  # forget the passphrase again
out = run("hook single", b"\x03", TS_CONFIG=f"{T}/locked.gpg")
check("Ctrl-C at the prompt cancels quietly", "cancelled" in out and "PICKED" not in out and "Traceback" not in out)

# init: the key menu: [existing keys..., create a new key, symmetric]
out = run("init", ENTER, TS_CONFIG=f"{T}/init1.gpg")
fpr = next(l.split(":")[9] for l in subprocess.run([*gpg, "--list-secret-keys", "--with-colons"], capture_output=True,
                                                   env=env, text=True).stdout.splitlines() if l.startswith("fpr"))
check("init: existing key listed", "ts@test" in out)
check("init: picked key written as gpg_recipient", f"gpg_recipient: {fpr}\n" in decrypt(f"{T}/init1.gpg"))

# keys now: ts@test, lock@test -> menu: [ts, lock, create, symmetric]
out = run("init", DOWN, DOWN, ENTER, b"New Person <new@test>\r", b"kpw\r", b"kpw\r", TS_CONFIG=f"{T}/init2.gpg")
check("init: new key's passphrase asked twice in the terminal", "New passphrase for the new key" in out and "Repeat it:" in out)
check("init: create a new key", "new@test" in subprocess.run([*gpg, "--list-secret-keys"], capture_output=True, env=env, text=True).stdout)
check("init: new key written as gpg_recipient", "gpg_recipient: New Person <new@test>" in decrypt(f"{T}/init2.gpg", "kpw"))
run("init", UP, ENTER, b"sym\r", b"sym\r", TS_CONFIG=f"{T}/init3.gpg")  # up from top wraps to the last item
check("init: symmetric = no gpg_recipient, our passphrase", "gpg_recipient" not in decrypt(f"{T}/init3.gpg", "sym"))
out = run("init", UP, ENTER, b"one\r", b"two\r", TS_CONFIG=f"{T}/init4.gpg")
check("init: mismatched passphrases -> nothing written", "don't match" in out and not os.path.exists(f"{T}/init4.gpg"))
print("ALL DONE")
