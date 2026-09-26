import re
import tkinter as tk

_DEEPLINK_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+\-.]*://.+')


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
        ("  Launch",        "launch"),
        ("  Force Stop",    "force-stop"),
        ("  Clear Data",    "clear-data"),
        ("  Uninstall",     "uninstall"),
        ("  Shared Prefs",  "shared-prefs"),
        ("  Remote Config", "remote-config"),
        ("  Litmus",        "litmus"),
        ("  Permissions",   "permissions"),
    ]

    def __init__(self, parent, packages, on_action, clipboard_text="", deeplink_history=None):
        self._packages         = packages
        self._on_action        = on_action
        self._state            = "search"   # "search" | "actions"
        self._selected_pkg     = None
        self._clipboard_text   = clipboard_text
        self._deeplink_history = deeplink_history or []
        self._virtual          = []   # [(display_str, type, value), ...]
        self._pkg_filtered     = list(packages)

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
        self._var.trace_add("write", lambda *_: self._on_query_change())
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

    def _on_query_change(self):
        if self._state == "viewer":
            self._filter_viewer(self._var.get())
        elif self._state == "input":
            pass  # free-text entry, no filtering
        else:
            self._filter(self._var.get())

    def _filter(self, query):
        q  = query.strip()
        ql = q.lower()

        self._virtual = []

        if _DEEPLINK_RE.match(q):
            # Deep link typed directly — show launch entry, skip package search
            self._virtual.append((f"  Launch: {q}", "deeplink", q))
            self._pkg_filtered = []
        else:
            if not ql or ql in "input text":
                self._virtual.append(("  Input Text — type to send to device", "input-text", ""))
            if ql and ql in "clipboard":
                preview = (self._clipboard_text[:60].replace('\n', ' ')
                           if self._clipboard_text else "(empty)")
                self._virtual.append((f"  Clipboard — {preview}", "clipboard", self._clipboard_text))
            if ql and ql in "deeplink":
                for url in self._deeplink_history[:10]:
                    self._virtual.append((f"  Link: {url}", "deeplink", url))
            self._pkg_filtered = (
                [p for p in self._packages if ql in p.lower()] if ql
                else list(self._packages)
            )

        self._listbox.delete(0, tk.END)
        for display, _, _ in self._virtual:
            self._listbox.insert(tk.END, display)
        for pkg in self._pkg_filtered[:60]:
            self._listbox.insert(tk.END, f"  {pkg}")

        total = len(self._virtual) + min(len(self._pkg_filtered), 60)
        if total:
            self._listbox.selection_set(0)
            self._footer.set(f"{total} results  |  Enter=select  Esc=close")
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
        if self._state == "input":
            text = self._var.get()
            if text:
                self._on_action("__input-text__", text)
                self._close()
            return "break"
        if self._state == "viewer":
            if self._viewer_on_select:
                cur = self._listbox.curselection()
                if cur:
                    key, value = self._viewer_shown[cur[0]]
                    self._viewer_on_select(key, value)
            return "break"
        if self._state == "search":
            cur = self._listbox.curselection()
            if not cur:
                return "break"
            idx = cur[0]
            if idx < len(self._virtual):
                _, vtype, vvalue = self._virtual[idx]
                if vtype == "input-text":
                    self._show_input_mode()
                else:
                    self._on_action(f"__{vtype}__", vvalue)
                    self._close()
            else:
                pkg_idx = idx - len(self._virtual)
                if pkg_idx < len(self._pkg_filtered):
                    pkg = self._pkg_filtered[pkg_idx]
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
        if action in ("shared-prefs", "remote-config", "litmus", "permissions"):
            self._enter_loading(action)
            self._on_action(self._selected_pkg, action)
        else:
            self._on_action(self._selected_pkg, action)
            self._close()

    def _enter_loading(self, label):
        self._state = "viewer"
        self._listbox.delete(0, tk.END)
        self._listbox.insert(tk.END, "  loading...")
        self._footer.set(f"{label} — fetching from device...")

    def show_viewer(self, title, items, on_select=None):
        """Called from app after data is fetched. items = [(key, value), ...]"""
        self._viewer_items    = items
        self._viewer_title    = title
        self._viewer_on_select = on_select
        self._viewer_shown    = []
        self._state = "viewer"
        self._entry.config(state="normal")
        self._var.set("")
        self.win.resizable(True, True)
        self.win.geometry(f"750x{self.H}")
        self._filter_viewer("")
        self._entry.focus_set()

    def _filter_viewer(self, query):
        ql = query.strip().lower()
        self._viewer_shown = [
            (k, v) for k, v in self._viewer_items
            if not ql or ql in k.lower() or ql in str(v).lower()
        ]
        self._listbox.delete(0, tk.END)
        for k, v in self._viewer_shown:
            self._listbox.insert(tk.END, f"  {k}  =  {v}")
        hint = "  Enter=expand" if self._viewer_on_select else ""
        n = len(self._viewer_shown)
        self._footer.set(f"{self._viewer_title} — {n} keys  |  type to search{hint}  Esc=back")

    def update_viewer_items(self, items):
        """Refresh viewer items in-place, preserving search query and selection."""
        cur = self._listbox.curselection()
        saved_idx = cur[0] if cur else 0
        self._viewer_items = items
        self._filter_viewer(self._var.get())
        n = self._listbox.size()
        if n:
            idx = min(saved_idx, n - 1)
            self._listbox.selection_set(idx)
            self._listbox.see(idx)

    def _back_to_actions(self):
        self.win.resizable(False, False)
        self.win.geometry(f"{self.W}x{self.H}")
        self._show_actions(self._selected_pkg)

    def _show_input_mode(self):
        self._state = "input"
        self._var.set("")
        self._listbox.delete(0, tk.END)
        self._listbox.insert(tk.END, "  (type text and press Enter to send to device)")
        self._entry.config(state="normal")
        self._entry.focus_set()
        self._footer.set("Type text  |  Enter=send to device  Esc=back")

    def _on_esc(self, _e=None):
        if self._state == "input":
            self._back_to_search()
        elif self._state == "viewer":
            self._back_to_actions()
        elif self._state == "actions":
            self._back_to_search()
        else:
            self._close()
        return "break"

    def _close(self, _e=None):
        self.win.destroy()
