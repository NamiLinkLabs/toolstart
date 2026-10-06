"""`toolstart edit` window (tkinter). The config is edited in memory only: no YAML, no temp file."""

import copy
import glob
import os
import re
import shlex
import sys
import tkinter as tk
from tkinter import font, messagebox, simpledialog, ttk

SEP = "\x1f"  # tree item ids: "settings", "t<SEP>tool", "p<SEP>tool<SEP>profile"
MASK = "•"
SYMMETRIC = "no key — symmetric passphrase (asked twice on every save)"


def use_base_tcl() -> None:
    """In a venv on uv's standalone Python (what `uv tool install` makes) Tcl looks for its library
    under the venv and dies with "Can't find a usable init.tcl". Point it at the base install."""
    if sys.prefix == sys.base_prefix:
        return
    for var, marker in (("TCL_LIBRARY", "init.tcl"), ("TK_LIBRARY", "tk.tcl")):
        found = sorted(glob.glob(os.path.join(sys.base_prefix, "*", var[:2].lower() + "*", marker)))
        if found and var not in os.environ:
            os.environ[var] = os.path.dirname(found[-1])


class EnvTable(ttk.Frame):
    """Editable NAME / value rows. Values stay masked until their row's `show` is pressed."""

    def __init__(self, master, title: str, env: dict):
        super().__init__(master)
        ttk.Label(self, text=title).pack(anchor="w")
        self.body = ttk.Frame(self)
        self.body.pack(fill="x")
        ttk.Button(self, text="+ variable", command=lambda: self.add("", "", focus=True)).pack(anchor="w", pady=4)
        self.rows: list[tuple[ttk.Frame, tk.StringVar, tk.StringVar]] = []
        for k, v in env.items():
            self.add(str(k), "" if v is None else str(v))

    def add(self, key: str, value: str, focus=False) -> None:
        frame, k, v = ttk.Frame(self.body), tk.StringVar(value=key), tk.StringVar(value=value)
        frame.pack(fill="x", pady=1)
        name = ttk.Entry(frame, textvariable=k, width=38)
        name.pack(side="left")
        val = ttk.Entry(frame, textvariable=v, show=MASK)
        val.pack(side="left", fill="x", expand=True, padx=4)

        def toggle():
            hidden = bool(val.cget("show"))
            val.configure(show="" if hidden else MASK)
            show.configure(text="hide" if hidden else "show")

        show = ttk.Button(frame, text="show", width=5, command=toggle)
        show.pack(side="left")
        row = (frame, k, v)
        ttk.Button(frame, text="✕", width=2, command=lambda: (frame.destroy(), self.rows.remove(row))).pack(side="left")
        self.rows.append(row)
        if focus:
            name.focus_set()

    def value(self) -> dict:
        """The rows as a dict (blank rows skipped). ValueError on a bad or duplicate name."""
        env = {}
        for _, k, v in self.rows:
            name = k.get().strip()
            if not name and not v.get():
                continue
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"'{name}' is not a valid variable name (letters, digits, _).")
            if name in env:
                raise ValueError(f"'{name}' is listed twice.")
            env[name] = v.get()
        return env


class Editor:
    """Tool/profile tree on the left, a form for the selected item on the right."""

    def __init__(self, load, on_save):
        """`load(ask)` -> (config, gpg keys), run once the window is up; `on_save(config, ask)`.
        `ask(prompt)` asks for a GPG passphrase in a masked dialog (None = cancelled)."""
        self.original = self.config = None
        self.failed: SystemExit | None = None  # a failed load, re-raised by run() after the window closes
        self.keys, self.on_save = [], on_save
        self.current, self.commit, self.table = None, lambda: None, None  # commit: open form -> self.config
        # Dialogs as attributes, so tests can swap them out.
        self.ask = lambda title, prompt: simpledialog.askstring(title, prompt, parent=self.root)
        self.confirm = lambda title, text: messagebox.askyesno(title, text, parent=self.root)
        self.ask_secret = lambda prompt: simpledialog.askstring("toolstart", prompt, show=MASK, parent=self.root)

        use_base_tcl()
        self.root = tk.Tk()
        self.root.title("toolstart — profiles")
        self.root.minsize(820, 460)
        self.bold = font.nametofont("TkDefaultFont").copy()
        self.bold.configure(size=14, weight="bold")
        left = ttk.Frame(self.root, padding=8)
        left.pack(side="left", fill="y")
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill="y", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show())
        self.buttons = [ttk.Button(left, text=text, command=command) for text, command in (
            ("+ tool", self.add_tool), ("+ profile", self.add_profile), ("rename", self.rename), ("delete", self.delete))]
        for button in self.buttons:
            button.pack(fill="x", pady=1)

        right = ttk.Frame(self.root, padding=8)
        right.pack(side="left", fill="both", expand=True)
        self.form = ttk.Frame(right)
        self.form.pack(fill="both", expand=True)
        bar = ttk.Frame(right)
        bar.pack(fill="x")
        self.status = ttk.Label(bar, foreground="#c0392b", wraplength=520)
        self.status.pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="Cancel", command=self.close).pack(side="right")
        self.buttons.append(ttk.Button(bar, text="Save", command=self.save))
        self.buttons[-1].pack(side="right", padx=4)
        for button in self.buttons:
            button.state(["disabled"])  # until the config is decrypted
        ttk.Label(self.form, text="Decrypting the config…").pack(anchor="w")

        self.root.bind("<Control-s>", lambda _: self.save())
        if self.root.tk.call("tk", "windowingsystem") == "aqua":  # macOS
            self.root.bind("<Command-s>", lambda _: self.save())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self.start, load)

    def start(self, load) -> None:
        """Raise the window, then decrypt — a passphrase, if needed, is asked in our own dialog."""
        self.to_front()
        try:
            config, self.keys = load(self.ask_secret)
        except SystemExit as e:  # failed decrypt / bad config: close; run() re-raises it
            self.failed = e
            self.destroy()
            return
        self.original, self.config = copy.deepcopy(config), copy.deepcopy(config)
        for button in self.buttons:
            button.state(["!disabled"])
        self.refresh("settings")

    def run(self) -> None:
        self.root.mainloop()
        if self.failed:
            raise self.failed

    def destroy(self) -> None:
        """Close the window, dropping pending `after` callbacks (they'd error on a dead window)."""
        for pending in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(pending)
        self.root.destroy()

    def to_front(self) -> None:
        """Started from a terminal, the window opens behind it: raise it and take focus."""
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        if sys.platform == "win32":  # focus_force alone is blocked by Windows' foreground lock
            from toolstart._win import raise_window
            raise_window(self.root)
        self.root.focus_force()

    # --- tree --------------------------------------------------------------

    def refresh(self, select: str) -> None:
        """Rebuild the tree from self.config (after add/rename/delete) and open `select`."""
        self.tree.delete(*self.tree.get_children())
        self.tree.insert("", "end", iid="settings", text="⚙ settings")
        for t, tool in self.config["tools"].items():
            self.tree.insert("", "end", iid=f"t{SEP}{t}", text=t, open=True)
            for p in tool["profiles"]:
                self.tree.insert(f"t{SEP}{t}", "end", iid=f"p{SEP}{t}{SEP}{p}", text=p)
        self.current, self.commit = None, lambda: None  # already committed by the caller
        self.tree.selection_set(select)
        self.tree.see(select)
        self.show()

    def committed(self) -> bool:
        """Write the open form back; on a bad value say why and keep the form open."""
        try:
            self.commit()
        except ValueError as e:
            self.status.configure(text=str(e))
            return False
        self.status.configure(text="")
        return True

    def show(self) -> None:
        sel = (self.tree.selection() or ("settings",))[0]
        if sel == self.current:
            return
        if not self.committed():
            self.tree.selection_set(self.current)  # stay on the form with the problem
            return
        for widget in self.form.winfo_children():
            widget.destroy()
        self.current, self.commit, self.table = sel, lambda: None, None
        kind, *names = sel.split(SEP)
        {"settings": self.form_settings, "t": self.form_tool, "p": self.form_profile}[kind](*names)

    def heading(self, text: str) -> None:
        ttk.Label(self.form, text=text, font=self.bold).pack(anchor="w", pady=(0, 8))

    # --- forms -------------------------------------------------------------

    def form_settings(self) -> None:
        cfg = self.config
        self.heading("Settings")
        labels = [f"{uid}  ({fpr[-16:]})" for fpr, uid in self.keys] + [SYMMETRIC]
        values = [fpr for fpr, _ in self.keys] + [None]
        if (cur := cfg.get("gpg_recipient")) not in values:  # e.g. a name/email instead of a fingerprint
            labels.insert(0, str(cur))
            values.insert(0, cur)
        ttk.Label(self.form, text="Encrypt the config to:").pack(anchor="w")
        enc = ttk.Combobox(self.form, state="readonly", values=labels)
        enc.current(values.index(cur))
        enc.pack(fill="x", pady=(0, 8))
        upd = tk.BooleanVar(value=cfg.get("update_check", True))
        ttk.Checkbutton(self.form, text="Check PyPI for new toolstart versions (every 6 h)", variable=upd).pack(anchor="w")
        self.table = EnvTable(self.form, "\nGlobal env — every profile gets these; a profile's own value wins:", cfg["env"])
        self.table.pack(fill="x")

        def commit():
            cfg["env"] = self.table.value()
            if recipient := values[enc.current()]:
                cfg["gpg_recipient"] = recipient
            else:
                cfg.pop("gpg_recipient", None)
            if upd.get():
                cfg.pop("update_check", None)
            else:
                cfg["update_check"] = False
        self.commit = commit

    def form_tool(self, t: str) -> None:
        self.heading(t)
        names = ", ".join(self.config["tools"][t]["profiles"]) or "none yet — add one with + profile"
        ttk.Label(self.form, justify="left", text=(
            f"Typing `{t}` in your shell opens the profile picker.\n"
            f"The name must match the real command.\n\nProfiles: {names}")).pack(anchor="w")

    def form_profile(self, t: str, p: str) -> None:
        prof = self.config["tools"][t]["profiles"][p]
        self.heading(f"{t} › {p}")
        ttk.Label(self.form, text="Command — whatever you type after the tool name is appended:").pack(anchor="w")
        orig = prof.get("cmd") or ""
        shown = orig if isinstance(orig, str) else shlex.join(map(str, orig))
        cmd = tk.StringVar(value=shown)
        ttk.Entry(self.form, textvariable=cmd).pack(fill="x", pady=(0, 8))
        self.table = EnvTable(self.form, 'Environment — a value like "$(helper --flag)" is replaced by its output:',
                              prof["env"])
        self.table.pack(fill="x")

        def commit():
            prof["env"] = self.table.value()
            text = cmd.get().strip()
            prof["cmd"] = orig if text == shown else text  # untouched list stays a list
        self.commit = commit

    # --- actions -----------------------------------------------------------

    def selected(self) -> tuple[str, ...]:
        kind, *names = (self.current or "settings").split(SEP)
        return (kind, *names)

    def new_name(self, title: str, prompt: str, taken) -> str | None:
        name = (self.ask(title, prompt) or "").strip()
        if not name:
            return None
        if not re.fullmatch(r"[\w.-]+", name):
            self.status.configure(text=f"'{name}': use letters, digits, '.', '_' or '-' only.")
        elif name in taken:
            self.status.configure(text=f"'{name}' already exists.")
        else:
            return name
        return None

    def add_tool(self) -> None:
        tools = self.config["tools"]
        if self.committed() and (t := self.new_name("New tool", "Command to intercept (e.g. cortex, claude, aws):", tools)):
            tools[t] = {"profiles": {"default": {"cmd": t, "env": {}}}}
            self.refresh(f"p{SEP}{t}{SEP}default")

    def add_profile(self) -> None:
        kind, *names = self.selected()
        if kind == "settings":
            self.status.configure(text="Select a tool first.")
            return
        profiles = self.config["tools"][names[0]]["profiles"]
        if self.committed() and (p := self.new_name("New profile", f"Profile name for {names[0]}:", profiles)):
            profiles[p] = {"cmd": names[0], "env": {}}
            self.refresh(f"p{SEP}{names[0]}{SEP}{p}")

    def rename(self) -> None:
        kind, *names = self.selected()
        if kind == "settings" or not self.committed():
            return
        tools = self.config["tools"]
        parent = tools if kind == "t" else tools[names[0]]["profiles"]
        old = names[-1]
        if new := self.new_name("Rename", f"New name for '{old}':", parent):
            renamed = {(new if k == old else k): v for k, v in parent.items()}  # keeps the order
            parent.clear()
            parent.update(renamed)
            self.refresh(f"t{SEP}{new}" if kind == "t" else f"p{SEP}{names[0]}{SEP}{new}")

    def delete(self) -> None:
        kind, *names = self.selected()
        if kind == "settings" or not self.confirm("Delete", f"Delete '{'/'.join(names)}' and its secrets?"):
            return
        tools = self.config["tools"]
        if kind == "t":
            del tools[names[0]]
        else:
            del tools[names[0]]["profiles"][names[1]]
        self.commit = lambda: None  # the open form belonged to what was just deleted
        self.refresh("settings" if kind == "t" else f"t{SEP}{names[0]}")

    def save(self) -> None:
        if self.config is None or not self.committed():  # e.g. Ctrl+S while still decrypting
            return
        for t, tool in self.config["tools"].items():
            if not tool["profiles"]:
                self.status.configure(text=f"'{t}' has no profiles — add one or delete the tool.")
                return
            for p, prof in tool["profiles"].items():
                if not prof.get("cmd"):
                    self.status.configure(text=f"'{t} › {p}' has no command.")
                    return
        try:  # a symmetric passphrase is asked now; on failure keep the window and the edits
            self.on_save(self.config, self.ask_secret)
        except SystemExit as e:
            self.status.configure(text=str(e.code))
            return
        self.destroy()

    def close(self) -> None:
        changed = not self.committed() or self.config != self.original
        if not changed or self.confirm("Discard changes?", "Close without saving?"):
            self.destroy()
