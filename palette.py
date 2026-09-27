import collections
import re
import threading
import tkinter as tk

import theme_manager
from inspectors.memory import MemoryFetcher, adj_label

_DEEPLINK_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+\-.]*://.+')


class CommandPalette:
    """Spotlight-style package launcher. Trigger: double Shift."""
    W, H   = 440, 380
    # Default colors — overridden at __init__ time by active theme
    BG       = "#1c1c1e"
    BG_IN    = "#2c2c2e"
    FG       = "#f0f0f0"
    FG_DIM   = "#888888"
    ACCENT   = "#00c864"
    SEL_BG   = "#3a3a3c"
    ERR      = "#ff4444"
    WARN     = "#ff8c35"
    INFO     = "#4fc3f7"
    GRAPH_BG = "#111111"
    GRID_LINE = "#333333"
    BAR_BG   = "#2a2a2a"

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

    def __init__(self, parent, packages, on_action, clipboard_text="", deeplink_history=None, serial=None):
        t = theme_manager.get_active_theme()
        self.BG        = t["bg"]
        self.BG_IN     = t["bg_input"]
        self.FG        = t["fg"]
        self.FG_DIM    = t["fg_dim"]
        self.ACCENT    = t["accent"]
        self.SEL_BG    = t["sel_bg"]
        self.ERR       = t["err"]
        self.WARN      = t["warn"]
        self.INFO      = t["info"]
        self.GRAPH_BG  = t["graph_bg"]
        self.GRID_LINE = t["grid_line"]
        self.BAR_BG    = t["bar_bg"]

        self._packages         = packages
        self._on_action        = on_action
        self._state            = "search"   # "search" | "actions" | "viewer" | "input" | "hierarchy" | "memory"
        self._selected_pkg     = None
        self._clipboard_text   = clipboard_text
        self._deeplink_history = deeplink_history or []
        self._virtual          = []   # [(display_str, type, value), ...]
        self._pkg_filtered     = list(packages)
        self._serial           = serial
        # memory watchdog state
        self._mem_polling       = False
        self._mem_stop          = threading.Event()
        self._mem_data          = {}
        self._mem_history       = collections.deque(maxlen=60)  # system used %
        self._java_heap_history = collections.deque(maxlen=60)  # java heap fill %
        self._mem_canvas        = None
        self._mem_fetcher       = None

        self.win = tk.Toplevel(parent)
        self.win.title("keytap — launch app")
        self.win.configure(bg=self.BG)
        self.win.resizable(True, True)

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

        tk.Frame(self.win, bg=self.GRID_LINE, height=1).pack(fill="x")

        self._content_frame = tk.Frame(self.win, bg=self.BG)
        self._content_frame.pack(fill="both", expand=True, padx=8, pady=4)

        self._listbox = tk.Listbox(
            self._content_frame, bg=self.BG, fg=self.FG,
            selectbackground=self.SEL_BG, selectforeground=self.ACCENT,
            relief="flat", bd=0, highlightthickness=0,
            font=("Menlo", 12), activestyle="none", height=13
        )
        self._listbox.pack(fill="both", expand=True)

        self._mem_canvas = tk.Canvas(self._content_frame, bg=self.BG, highlightthickness=0)
        # not packed until memory state is entered

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
        self._listbox.bind("<<ListboxSelect>>", self._on_listbox_select)
        self.win.bind("<Escape>", self._on_esc)
        self.win.bind("<Command-r>", self._on_ctrl_r)

    def _on_query_change(self):
        if self._state == "viewer":
            self._filter_viewer(self._var.get())
        elif self._state == "input":
            pass  # free-text entry, no filtering
        elif self._state == "hierarchy":
            self._filter_hierarchy(self._var.get())
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
            if not ql or ql in "view hierarchy":
                self._virtual.append(("  View Hierarchy — browse UI tree", "view-hierarchy", ""))
            if not ql or ql in "memory watchdog":
                self._virtual.append(("  Memory Watchdog — live RAM stats", "memory-watchdog", ""))
            if not ql or ql in "input text":
                self._virtual.append(("  Input Text — type to send to device", "input-text", ""))
            if not ql or ql in "theme":
                self._virtual.append(("  Theme — switch color theme", "theme-switcher", ""))
            if not ql or ql in "developer options":
                self._virtual.append(("  Developer Options — toggle ADB debug settings", "dev-options", ""))
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
            self._on_listbox_select()
        return "break"

    def _down(self, _e):
        cur = self._listbox.curselection()
        nxt = (cur[0] + 1) if cur else 0
        if nxt < self._listbox.size():
            self._listbox.selection_clear(0, tk.END)
            self._listbox.selection_set(nxt)
            self._listbox.see(nxt)
            self._on_listbox_select()
        return "break"

    def _select(self, _e=None):
        if self._state == "input":
            text = self._var.get()
            if text:
                self._on_action("__input-text__", text)
                self._close()
            return "break"
        if self._state == "hierarchy":
            cur = self._listbox.curselection()
            if not cur:
                return "break"
            node = self._hier_shown[cur[0]]
            if node['children']:
                self._hier_nav_stack.append((self._hier_current_nodes, self._hier_current_label))
                label = (node['resource_id'] or
                         (f'"{node["text"][:20]}"' if node['text'] else node['class_name']))
                self._hier_current_nodes = node['children']
                self._hier_current_label = label
                self._var.set("")
                self._hier_show_level()
            else:
                if node['cx'] or node['cy']:
                    self._on_action("__tap-hierarchy__", f"{node['cx']},{node['cy']}")
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
                elif vtype == "view-hierarchy":
                    self._enter_hierarchy_loading()
                    self._on_action("__view-hierarchy__", "")
                elif vtype == "memory-watchdog":
                    self._mem_enter()
                elif vtype == "theme-switcher":
                    self._show_theme_picker()
                elif vtype == "dev-options":
                    self._enter_loading("Dev Options")
                    self._on_action("__dev-options__", "")
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

    def _show_theme_picker(self):
        themes = theme_manager.list_themes()
        current = theme_manager.active_name()
        items = [(f"* {t.title()}" if t == current else f"  {t.title()}", t) for t in themes]

        def on_select(_, theme_name):
            theme_manager.set_theme(theme_name)
            self._on_action("__set-theme__", theme_name)
            self._close()

        self.show_viewer("Theme", items, on_select=on_select)

    def _on_listbox_select(self, _e=None):
        if self._state == "hierarchy" and getattr(self, '_hier_on_hover', None):
            cur = self._listbox.curselection()
            if cur and cur[0] < len(self._hier_shown):
                self._hier_on_hover(self._hier_shown[cur[0]].get('bounds'))
            else:
                self._hier_on_hover(None)

    def _on_ctrl_r(self, _e=None):
        if self._state == "hierarchy":
            self._on_action("__refresh-hierarchy__", "")
        elif self._state == "memory":
            self._mem_restart()
        return "break"

    def _on_esc(self, _e=None):
        if self._state == "memory":
            self._mem_exit()
        elif self._state == "input":
            self._back_to_search()
        elif self._state == "hierarchy":
            if self._var.get():
                self._var.set("")
                self._hier_show_level()
            elif self._hier_nav_stack:
                self._hier_current_nodes, self._hier_current_label = self._hier_nav_stack.pop()
                self._hier_show_level()
            else:
                self._hier_clear_hover()
                self.win.geometry(f"{self.W}x{self.H}")
                self._back_to_search()
        elif self._state == "viewer":
            if self._selected_pkg:
                self._back_to_actions()
            else:
                self.win.geometry(f"{self.W}x{self.H}")
                self._back_to_search()
        elif self._state == "actions":
            self._back_to_search()
        else:
            self._close()
        return "break"

    def _hier_clear_hover(self):
        if getattr(self, '_hier_on_hover', None):
            self._hier_on_hover(None)

    def _enter_hierarchy_loading(self):
        self._state = "hierarchy"
        self._hier_nav_stack = []
        self._hier_current_nodes = []
        self._hier_current_label = "root"
        self._hier_all_flat = []
        self._hier_shown = []
        self._entry.config(state="disabled")
        self._listbox.delete(0, tk.END)
        self._listbox.insert(tk.END, "  loading hierarchy...")
        self._footer.set("fetching UI hierarchy from device...")

    def show_hierarchy(self, root_node, flat_nodes, on_hover=None):
        """Called from app after hierarchy is fetched."""
        self._hier_nav_stack = []
        self._hier_all_flat = flat_nodes
        self._hier_current_nodes = root_node['children'] or [root_node]
        self._hier_current_label = "root"
        self._hier_on_hover = on_hover
        self._state = "hierarchy"
        self._entry.config(state="normal")
        self._var.set("")
        self.win.geometry(f"750x{self.H}")
        self._hier_show_level()
        self._entry.focus_set()

    def _hier_show_level(self):
        self._hier_shown = list(self._hier_current_nodes)
        self._listbox.delete(0, tk.END)
        for n in self._hier_shown:
            self._listbox.insert(tk.END, self._hier_node_label(n))
        if self._hier_shown:
            self._listbox.selection_set(0)
            if getattr(self, '_hier_on_hover', None):
                self._hier_on_hover(self._hier_shown[0].get('bounds'))
        self._hier_update_footer()

    def _hier_node_label(self, node):
        cls = node['class_name'] or '?'
        text = f' "{node["text"][:28]}"' if node['text'] else ''
        rid = f' [{node["resource_id"]}]' if node['resource_id'] else ''
        n_ch = len(node['children'])
        if n_ch:
            hint = f' ({n_ch})'
        elif node['clickable']:
            hint = ' ·tap'
        else:
            hint = ''
        return f"  {cls}{text}{rid}{hint}"

    def _filter_hierarchy(self, query):
        ql = query.strip().lower()
        if not ql:
            self._hier_shown = list(self._hier_current_nodes)
        else:
            self._hier_shown = [
                n for n in self._hier_all_flat
                if ql in n['text'].lower()
                or ql in n['resource_id'].lower()
                or ql in n['class_name'].lower()
                or ql in n['content_desc'].lower()
            ]
        self._listbox.delete(0, tk.END)
        for n in self._hier_shown:
            self._listbox.insert(tk.END, self._hier_node_label(n))
        if self._hier_shown:
            self._listbox.selection_set(0)
            if getattr(self, '_hier_on_hover', None):
                self._hier_on_hover(self._hier_shown[0].get('bounds'))
        elif getattr(self, '_hier_on_hover', None):
            self._hier_on_hover(None)
        self._hier_update_footer(searching=bool(ql))

    def _hier_update_footer(self, searching=False):
        path_parts = [lbl for _, lbl in self._hier_nav_stack] + [self._hier_current_label]
        path = " > ".join(path_parts[-4:])
        n = len(self._hier_shown)
        scope = " (all)" if searching else ""
        self._footer.set(
            f"{path}  |  {n} nodes{scope}  Enter=expand/tap  Cmd+R=refresh  Esc=up"
        )

    def _close(self, _e=None):
        self._hier_clear_hover()
        self._mem_stop_poll()
        self.win.destroy()

    # ── Memory Watchdog ──────────────────────────────────────────────────────

    def _mem_enter(self):
        self._state = "memory"
        self._entry.config(state="disabled")
        self.win.geometry(f"620x480")
        self._listbox.pack_forget()
        self._mem_canvas.pack(fill="both", expand=True)
        self._footer.set("Cmd+R=refresh  Esc=exit")
        self._mem_history.clear()
        self._java_heap_history.clear()
        self._mem_data = {}
        self._mem_canvas.delete("all")
        self._mem_canvas.create_text(310, 130, text="fetching memory stats...",
                                     fill=self.FG_DIM, font=("Menlo", 11))
        self._mem_stop.clear()
        self._mem_polling = True
        self._mem_fetcher = MemoryFetcher(self._serial)
        threading.Thread(target=self._mem_poll_loop, daemon=True).start()

    def _mem_exit(self):
        self._mem_stop_poll()
        self._mem_canvas.pack_forget()
        self._listbox.pack(fill="both", expand=True)
        self.win.geometry(f"{self.W}x{self.H}")
        self._back_to_search()

    def _mem_stop_poll(self):
        self._mem_polling = False
        self._mem_stop.set()

    def _mem_restart(self):
        self._mem_stop_poll()
        self._mem_history.clear()
        self._java_heap_history.clear()
        self._mem_data = {}
        self._mem_canvas.delete("all")
        self._mem_canvas.create_text(310, 130, text="refreshing...",
                                     fill=self.FG_DIM, font=("Menlo", 11))
        self._mem_stop.clear()
        self._mem_polling = True
        self._mem_fetcher = MemoryFetcher(self._serial)
        threading.Thread(target=self._mem_poll_loop, daemon=True).start()

    def _mem_poll_loop(self):
        u2_dev = None
        try:
            import uiautomator2 as u2
            u2_dev = u2.connect(self._serial) if self._serial else u2.connect()
        except Exception:
            pass

        while not self._mem_stop.is_set():
            data = self._mem_fetcher.fetch(u2_dev)
            self._mem_data = data
            if data.get('used_pct') is not None:
                self._mem_history.append(data['used_pct'])
            if data.get('java_heap_pct') is not None:
                self._java_heap_history.append(data['java_heap_pct'])
            try:
                self.win.after(0, self._mem_draw)
            except Exception:
                break
            self._mem_stop.wait(2.0)

    def _mem_draw(self):
        if self._state != "memory":
            return
        c = self._mem_canvas
        c.delete("all")
        d = self._mem_data
        W = max(c.winfo_width(), 580)

        if not d:
            c.create_text(W // 2, 120, text="loading...",
                          fill=self.FG_DIM, font=("Menlo", 11))
            return
        if d.get('error'):
            c.create_text(W // 2, 120, text=f"Error: {d['error']}",
                          fill=self.ERR, font=("Menlo", 11), width=W - 40)
            return

        pkg = d.get('app_pkg', '')

        y = 14
        # ── Title ─────────────────────────────────────────────────────────────
        title = f"MEMORY WATCHDOG  —  {pkg}" if pkg else "MEMORY WATCHDOG"
        c.create_text(W // 2, y, text=title,
                      fill=self.FG, font=("Menlo", 12, "bold"), anchor="n")
        y += 30

        # ── Two panels: GC Pressure (left)  |  Death Risk (right) ────────────
        mid = W // 2 - 8
        pad = 16

        # --- GC Pressure panel ------------------------------------------------
        java_used  = d.get('java_heap_used_mb', 0)
        java_total = d.get('java_heap_total_mb', 0)
        java_pct   = d.get('java_heap_pct') or 0.0
        native_mb  = d.get('native_heap_mb', 0)

        gc_color = (self.ERR if java_pct > 85 else
                    self.WARN if java_pct > 65 else self.ACCENT)

        c.create_text(pad, y, text="GC PRESSURE", fill=self.FG_DIM,
                      font=("Menlo", 10), anchor="nw")
        y += 18
        # bar
        bw = mid - pad - 8
        bfill = int(bw * java_pct / 100)
        c.create_rectangle(pad, y, pad + bw, y + 14, fill=self.BAR_BG, outline=self.GRID_LINE)
        if bfill > 0:
            c.create_rectangle(pad, y, pad + bfill, y + 14, fill=gc_color, outline="")
        c.create_text(pad + bw + 6, y + 1, text=f"{java_pct:.0f}%",
                      fill=gc_color, font=("Menlo", 10), anchor="nw")
        y += 20
        if java_total:
            c.create_text(pad, y, text=f"Java Heap: {java_used}/{java_total} MB",
                          fill=self.FG, font=("Menlo", 11), anchor="nw")
            y += 17
        if native_mb:
            c.create_text(pad, y, text=f"Native Heap: {native_mb} MB",
                          fill=self.FG_DIM, font=("Menlo", 11), anchor="nw")
            y += 17

        # --- Death Risk panel (right, same top) --------------------------------
        adj       = d.get('oom_adj')
        risk      = d.get('death_risk', 'Unknown')
        risk_color = d.get('death_risk_color', self.FG_DIM)
        rss_mb    = d.get('rss_mb', 0)

        ry = y - (17 * (1 + bool(native_mb) + bool(java_total))) - 20 - 18
        # reset right-panel y to match left panel top
        ry_start = 14 + 30 + 18  # after title + gap + label
        rx = mid + 16

        c.create_text(rx, ry_start, text="DEATH RISK", fill=self.FG_DIM,
                      font=("Menlo", 10), anchor="nw")
        # badge
        badge_y = ry_start + 14
        badge_w, badge_h = 120, 26
        c.create_rectangle(rx, badge_y, rx + badge_w, badge_y + badge_h,
                            fill=risk_color, outline="")
        c.create_text(rx + badge_w // 2, badge_y + badge_h // 2,
                      text=risk.upper(), fill="#000000" if risk == "Safe" else "#ffffff",
                      font=("Menlo", 11, "bold"), anchor="center")
        detail_y = badge_y + badge_h + 6
        if adj is not None:
            c.create_text(rx, detail_y,
                          text=f"adj {adj}  ({adj_label(adj)})",
                          fill=self.FG, font=("Menlo", 11), anchor="nw")
            detail_y += 17
        if rss_mb:
            c.create_text(rx, detail_y, text=f"RSS: {rss_mb} MB",
                          fill=self.FG_DIM, font=("Menlo", 11), anchor="nw")

        # vertical divider
        div_top = 14 + 30 + 14
        div_bot = y + 4
        c.create_line(mid, div_top, mid, div_bot, fill=self.GRID_LINE)

        # ── System RAM bar ─────────────────────────────────────────────────────
        y += 12
        total = d.get('total_mb', 0)
        avail = d.get('avail_mb', 0)
        used  = d.get('used_mb', 0)
        pct   = d.get('used_pct', 0.0)
        sys_color = self.ERR if pct > 80 else self.WARN if pct > 60 else self.ACCENT

        c.create_line(pad, y, W - pad, y, fill=self.GRID_LINE)
        y += 10
        c.create_text(pad, y, text="System RAM", fill=self.FG_DIM,
                      font=("Menlo", 10), anchor="nw")
        c.create_text(W - pad, y,
                      text=f"{used:,} / {total:,} MB  ({pct:.0f}%)",
                      fill=self.FG, font=("Menlo", 10), anchor="ne")
        y += 16
        sbw = W - pad * 2
        sfill = int(sbw * pct / 100)
        c.create_rectangle(pad, y, pad + sbw, y + 10, fill=self.BAR_BG, outline=self.GRID_LINE)
        if sfill > 0:
            c.create_rectangle(pad, y, pad + sfill, y + 10, fill=sys_color, outline="")
        y += 18

        # ── Dual sparklines ───────────────────────────────────────────────────
        sys_hist  = list(self._mem_history)
        heap_hist = list(self._java_heap_history)
        gh = 44  # graph height each

        for label, hist, line_col in [
            ("System used %", sys_hist, sys_color),
            ("Java heap fill %", heap_hist,
             self.ERR if heap_hist and heap_hist[-1] > 85
             else self.WARN if heap_hist and heap_hist[-1] > 65
             else self.INFO),
        ]:
            if len(hist) < 2:
                continue
            y += 4
            c.create_text(pad, y, text=label, fill=self.FG_DIM,
                          font=("Menlo", 9), anchor="nw")
            y += 13
            gx0, gx1, gy0, gy1 = pad, W - pad, y, y + gh
            c.create_rectangle(gx0, gy0, gx1, gy1, fill=self.GRAPH_BG, outline=self.GRID_LINE)
            gw = gx1 - gx0
            for pline in (25, 50, 75):
                ly = gy0 + int(gh * (1 - pline / 100))
                c.create_line(gx0, ly, gx1, ly, fill=self.BAR_BG, dash=(2, 4))
            n = len(hist)
            pts = []
            for i, v in enumerate(hist):
                px = gx0 + int(i * gw / max(n - 1, 1))
                py = gy0 + int(gh * (1 - v / 100))
                pts.extend([px, py])
            c.create_line(pts, fill=line_col, width=2, smooth=True)
            y += gh + 2
