"""Drive the `toolstart edit` window programmatically (no clicks). Run: python tests/test_gui.py
Needs tkinter and a display (macOS/Windows desktop, or xvfb-run on Linux)."""

import sys
import time

from toolstart.gui import MASK, SEP, SYMMETRIC, Editor

FPR = "A" * 40
CONFIG = {
    "gpg_recipient": FPR,
    "env": {"G": "global"},
    "tools": {"cortex": {"profiles": {"np": {"cmd": ["cortex", "-c", "np"], "env": {"PW": "s3cret"}}}}},
}
saved, answers, asked = [], [], []


def make(load=lambda ask: (CONFIG, [(FPR, "Test <t@test>")])) -> Editor:
    """Open an editor (dialogs answered from `answers`) and wait until load() has filled it in."""
    ed = Editor(load, on_save=lambda c, ask: saved.append(c))
    ed.root.withdraw()
    ed.ask, ed.confirm = (lambda title, prompt: answers.pop(0)), (lambda title, text: True)
    ed.ask_secret = lambda prompt: (asked.append(prompt), "pw")[1]
    while ed.config is None:
        ed.root.update()
        time.sleep(0.02)
    return ed


def check(name: str, ok: bool) -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


def open_item(iid: str) -> None:
    ed.tree.selection_set(iid)
    ed.root.update()


ed = Editor(lambda ask: (CONFIG, []), on_save=lambda c, ask: saved.append(c))
ed.root.withdraw()
check("buttons disabled until decrypted", all(b.instate(["disabled"]) for b in ed.buttons))
ed.save()
check("Ctrl+S before that does nothing", saved == [])
while ed.config is None:  # let the scheduled load run before closing
    ed.root.update()
ed.destroy()

ed = make(load=lambda ask: (ask("GPG passphrase:"), (CONFIG, []))[1])
check("passphrase asked in the editor's own dialog", asked == ["GPG passphrase:"])
check("buttons enabled once loaded", not any(b.instate(["disabled"]) for b in ed.buttons))
ed.destroy()

ed = Editor(lambda ask: sys.exit("toolstart: GPG decryption failed"), on_save=lambda c, ask: None)
ed.root.withdraw()
try:
    ed.run()
    check("failed decrypt closes the window with its message", False)
except SystemExit as e:
    check("failed decrypt closes the window with its message", e.code == "toolstart: GPG decryption failed")

ed = make()
open_item(f"p{SEP}cortex{SEP}np")
(_, key, value), = ed.table.rows
check("profile form shows env", (key.get(), value.get()) == ("PW", "s3cret"))
entry = ed.table.rows[0][0].winfo_children()[1]
check("secret value masked", entry.cget("show") == MASK)

value.set("n3w")
ed.table.add("EXTRA", "x")
ed.save()
check("save: env edited + added, untouched list cmd stays a list",
      saved[-1]["tools"]["cortex"]["profiles"]["np"] == {"cmd": ["cortex", "-c", "np"], "env": {"PW": "n3w", "EXTRA": "x"}})
check("save: input config not mutated", CONFIG["tools"]["cortex"]["profiles"]["np"]["env"] == {"PW": "s3cret"})

ed = make()
open_item(f"p{SEP}cortex{SEP}np")
ed.table.add("PW", "dup")
ed.save()
check("duplicate name blocks save, says why", len(saved) == 1 and "twice" in ed.status.cget("text"))
ed.table.rows[-1][1].set("BAD NAME")
open_item("settings")
check("bad name keeps the form open", ed.current == f"p{SEP}cortex{SEP}np" and "not a valid" in ed.status.cget("text"))
ed.table.rows[-1][0].winfo_children()[-1].invoke()  # ✕ on the bad row

answers += ["claude"]
ed.add_tool()
check("+ tool: default profile running the tool", ed.config["tools"]["claude"]["profiles"] == {"default": {"cmd": "claude", "env": {}}})
answers += ["work"]
ed.add_profile()
check("+ profile under the selected tool", list(ed.config["tools"]["claude"]["profiles"]) == ["default", "work"])
answers += ["claude"]
open_item(f"t{SEP}cortex")
ed.rename()
check("rename to an existing name refused", "already exists" in ed.status.cget("text"))
answers += ["cx"]
ed.rename()
check("rename keeps order", list(ed.config["tools"]) == ["cx", "claude"])
open_item(f"p{SEP}claude{SEP}work")
ed.delete()
check("delete profile", list(ed.config["tools"]["claude"]["profiles"]) == ["default"])

open_item("settings")
enc = [w for w in ed.form.winfo_children() if w.winfo_class() == "TCombobox"][0]
enc.current(enc.cget("values").index(SYMMETRIC))
ed.save()
check("settings: symmetric drops gpg_recipient, global env kept",
      "gpg_recipient" not in saved[-1] and saved[-1]["env"] == {"G": "global"})
check("save: cmd edits/renames all in", list(saved[-1]["tools"]) == ["cx", "claude"])
print("ALL DONE")
