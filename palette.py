import tkinter as tk


class CommandPalette:
    """Spotlight-style package launcher. Trigger: double Shift."""
    W, H   = 440, 380
    BG     = "#1c1c1e"
    BG_IN  = "#2c2c2e"
    FG     = "#f0f0f0"
    FG_DIM = "#888888"
    ACCENT = "#00c864"
    SEL_BG = "#3a3a3c"

    ACTIONS = [
        ("  Launch",      "launch"),
        ("  Force Stop",  "force-stop"),
        ("  Clear Data",  "clear-data"),
        ("  Uninstall",   "uninstall"),
    ]

    def __init__(self, parent, packages, on_action):
        self._packages     = packages
        self._filtered     = list(packages)
        self._on_action    = on_action
        self._state        = "search"   # "search" | "actions"
        self._selected_pkg = None

        self.win = tk.Toplevel(parent)
        self.win.title("keytap — launch app")
        self.win.configure(bg=self.BG)
        self.win.resizable(False, False)

        px, py = parent.winfo_x(), parent.winfo_y()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x = px + (pw - self.W) // 2
        y = py + (ph - self.H) // 2
        self.win.geometry(f"{self.W}x{self.H}+{x}+{y}")

        self._build()
        self._filter("")
        self.win.focus_force()
        self._entry.focus_set()

    def _build(self):
        row = tk.Frame(self.win, bg=self.BG_IN)
        row.pack(fill="x", padx=12, pady=(12, 0))
        tk.Label(row, text=">", bg=self.BG_IN, fg=self.FG_DIM,
                 font=("Menlo", 13)).pack(side="left", padx=(8, 4))
        self._var = tk.StringVar()
        self._var.trace_add("write", lambda *_: self._filter(self._var.get()))
        self._entry = tk.Entry(
            row, textvariable=self._var,
            bg=self.BG_IN, fg=self.FG, insertbackground=self.FG,
            relief="flat", font=("Menlo", 13), bd=0
        )
        self._entry.pack(fill="x", padx=(0, 8), pady=8, expand=True)

        tk.Frame(self.win, bg="#3a3a3c", height=1).pack(fill="x")

        self._listbox = tk.Listbox(
            self.win, bg=self.BG, fg=self.FG,
            selectbackground=self.SEL_BG, selectforeground=self.ACCENT,
            relief="flat", bd=0, highlightthickness=0,
            font=("Menlo", 12), activestyle="none", height=13
        )
        self._listbox.pack(fill="both", expand=True, padx=8, pady=4)

        self._footer = tk.StringVar()
        tk.Label(self.win, textvariable=self._footer,
                 bg=self.BG, fg=self.FG_DIM, font=("Menlo", 10),
                 anchor="w", padx=14).pack(fill="x", pady=(0, 8))

        for w in (self._entry, self._listbox):
            w.bind("<Up>",     self._up)
            w.bind("<Down>",   self._down)
            w.bind("<Return>", self._select)
            w.bind("<Escape>", self._on_esc)
        self._listbox.bind("<Double-Button-1>", self._select)
        self.win.bind("<Escape>", self._on_esc)

    def _filter(self, query):
        q = query.strip().lower()
        self._filtered = [p for p in self._packages if q in p.lower()] if q else list(self._packages)
        self._listbox.delete(0, tk.END)
        for pkg in self._filtered[:60]:
            self._listbox.insert(tk.END, f"  {pkg}")
        if self._filtered:
            self._listbox.selection_set(0)
            self._footer.set(f"{len(self._filtered)} packages  |  Enter=select  Esc=close")
        else:
            self._footer.set("no match")

    def _up(self, _e):
        cur = self._listbox.curselection()
        if cur and cur[0] > 0:
            self._listbox.selection_clear(0, tk.END)
            self._listbox.selection_set(cur[0] - 1)
            self._listbox.see(cur[0] - 1)
        return "break"

    def _down(self, _e):
        cur = self._listbox.curselection()
        nxt = (cur[0] + 1) if cur else 0
        if nxt < self._listbox.size():
            self._listbox.selection_clear(0, tk.END)
            self._listbox.selection_set(nxt)
            self._listbox.see(nxt)
        return "break"

    def _select(self, _e=None):
        if self._state == "search":
            pkg = self._filtered[self._listbox.curselection()[0]] if self._listbox.curselection() else None
            if pkg:
                self._selected_pkg = pkg
                self._show_actions(pkg)
        else:
            self._execute_action()
        return "break"

    def _show_actions(self, pkg):
        self._state = "actions"
        self._entry.config(state="disabled")
        self._var.set("")
        self._listbox.delete(0, tk.END)
        for label, _ in self.ACTIONS:
            self._listbox.insert(tk.END, label)
        self._listbox.selection_set(0)
        self._listbox.focus_set()
        short = pkg if len(pkg) <= 40 else "..." + pkg[-37:]
        self._footer.set(f"{short}  |  Enter=execute  Esc=back")

    def _back_to_search(self):
        self._state = "search"
        self._selected_pkg = None
        self._entry.config(state="normal")
        self._filter(self._var.get())
        self._entry.focus_set()

    def _execute_action(self):
        cur = self._listbox.curselection()
        if not cur:
            return
        _, action = self.ACTIONS[cur[0]]
        self._on_action(self._selected_pkg, action)
        self._close()

    def _on_esc(self, _e=None):
        if self._state == "actions":
            self._back_to_search()
        else:
            self._close()
        return "break"

    def _close(self, _e=None):
        self.win.destroy()
