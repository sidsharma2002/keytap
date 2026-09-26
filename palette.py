import collections
import re
import subprocess
import threading
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

    def __init__(self, parent, packages, on_action, clipboard_text="", deeplink_history=None, serial=None):
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
        self._mem_polling  = False
        self._mem_stop     = threading.Event()
        self._mem_data     = {}
        self._mem_history  = collections.deque(maxlen=60)
        self._mem_canvas   = None

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
                self.win.resizable(False, False)
                self.win.geometry(f"{self.W}x{self.H}")
                self._back_to_search()
        elif self._state == "viewer":
            self._back_to_actions()
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
        self.win.resizable(True, True)
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
        self.win.resizable(True, True)
        self.win.geometry(f"620x{self.H}")
        self._listbox.pack_forget()
        self._mem_canvas.pack(fill="both", expand=True)
        self._footer.set("Cmd+R=refresh  Esc=exit")
        self._mem_history.clear()
        self._mem_data = {}
        self._mem_canvas.delete("all")
        self._mem_canvas.create_text(310, 130, text="fetching memory stats...",
                                     fill=self.FG_DIM, font=("Menlo", 11))
        self._mem_stop.clear()
        self._mem_polling = True
        threading.Thread(target=self._mem_poll_loop, daemon=True).start()

    def _mem_exit(self):
        self._mem_stop_poll()
        self._mem_canvas.pack_forget()
        self._listbox.pack(fill="both", expand=True)
        self.win.resizable(False, False)
        self.win.geometry(f"{self.W}x{self.H}")
        self._back_to_search()

    def _mem_stop_poll(self):
        self._mem_polling = False
        self._mem_stop.set()

    def _mem_restart(self):
        self._mem_stop_poll()
        self._mem_history.clear()
        self._mem_data = {}
        self._mem_canvas.delete("all")
        self._mem_canvas.create_text(310, 130, text="refreshing...",
                                     fill=self.FG_DIM, font=("Menlo", 11))
        self._mem_stop.clear()
        self._mem_polling = True
        threading.Thread(target=self._mem_poll_loop, daemon=True).start()

    def _mem_poll_loop(self):
        u2_dev = None
        try:
            import uiautomator2 as u2
            u2_dev = u2.connect(self._serial) if self._serial else u2.connect()
        except Exception:
            pass

        while not self._mem_stop.is_set():
            data = self._mem_fetch(u2_dev)
            self._mem_data = data
            if data.get('used_pct') is not None:
                self._mem_history.append(data['used_pct'])
            try:
                self.win.after(0, self._mem_draw)
            except Exception:
                break
            self._mem_stop.wait(2.0)

    def _mem_fetch(self, u2_dev=None):
        try:
            # System memory
            sys_out = self._adb_shell_cmd('cat /proc/meminfo', u2_dev)
            meminfo = {}
            for line in sys_out.splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    nums = re.findall(r'\d+', v)
                    if nums:
                        meminfo[k.strip()] = int(nums[0])
            total_kb = meminfo.get('MemTotal', 0)
            avail_kb = meminfo.get('MemAvailable', meminfo.get('MemFree', 0))
            used_kb  = max(0, total_kb - avail_kb)
            used_pct = (used_kb / total_kb * 100) if total_kb else 0.0

            data = {
                'total_mb': total_kb // 1024,
                'avail_mb': avail_kb // 1024,
                'used_mb':  used_kb  // 1024,
                'used_pct': used_pct,
                'app_pkg':  '',
                'app_pss_mb': 0,
                'error': None,
            }

            # Foreground app
            fg_out = self._adb_shell_cmd('dumpsys window | grep mCurrentFocus', u2_dev)
            m = re.search(r'u\d+\s+([\w.]+)/', fg_out)
            if m:
                pkg = m.group(1)
                data['app_pkg'] = pkg
                pss_out = self._adb_shell_cmd(f'dumpsys meminfo {pkg}', u2_dev)
                data['app_pss_mb'] = self._parse_pss(pss_out)

            return data
        except Exception as e:
            return {
                'error': str(e), 'total_mb': 0, 'avail_mb': 0,
                'used_mb': 0, 'used_pct': 0.0, 'app_pkg': '', 'app_pss_mb': 0,
            }

    def _adb_shell_cmd(self, cmd, u2_dev=None):
        if u2_dev:
            try:
                result = u2_dev.shell(cmd)
                return result.output if hasattr(result, 'output') else str(result)
            except Exception:
                pass
        args = ['adb']
        if self._serial:
            args += ['-s', self._serial]
        args += ['shell', cmd]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=10)
            return result.stdout
        except Exception:
            return ''

    def _parse_pss(self, text):
        for line in text.splitlines():
            s = line.strip()
            if re.match(r'TOTAL\s+PSS', s, re.IGNORECASE):
                nums = re.findall(r'\d+', s)
                if nums:
                    return int(nums[0]) // 1024
        for line in text.splitlines():
            s = line.strip()
            if re.match(r'TOTAL\b', s, re.IGNORECASE):
                nums = re.findall(r'\d+', s)
                if nums and int(nums[0]) > 0:
                    return int(nums[0]) // 1024
        return 0

    def _mem_draw(self):
        if self._state != "memory":
            return
        c = self._mem_canvas
        c.delete("all")
        d = self._mem_data
        W = max(c.winfo_width(), 580)
        H = max(c.winfo_height(), 260)

        if not d:
            c.create_text(W // 2, H // 2, text="loading...",
                          fill=self.FG_DIM, font=("Menlo", 11))
            return
        if d.get('error'):
            c.create_text(W // 2, H // 2, text=f"Error: {d['error']}",
                          fill="#ff4444", font=("Menlo", 11), width=W - 40)
            return

        y = 16
        # Title
        c.create_text(W // 2, y, text="MEMORY WATCHDOG",
                      fill=self.FG, font=("Menlo", 13, "bold"), anchor="n")
        y += 34

        # System stats
        total = d.get('total_mb', 0)
        used  = d.get('used_mb',  0)
        avail = d.get('avail_mb', 0)
        pct   = d.get('used_pct', 0.0)
        c.create_text(16, y, text="System RAM", fill=self.FG_DIM,
                      font=("Menlo", 10), anchor="nw")
        y += 18
        stats = f"Total: {total:,} MB    Used: {used:,} MB    Available: {avail:,} MB    ({pct:.0f}%)"
        c.create_text(16, y, text=stats, fill=self.FG,
                      font=("Menlo", 11), anchor="nw")
        y += 22

        # Usage bar
        bx0, bx1 = 16, W - 16
        bw = bx1 - bx0
        used_w = int(bw * pct / 100)
        bar_color = "#ff4444" if pct > 80 else "#ff8c35" if pct > 60 else self.ACCENT
        c.create_rectangle(bx0, y, bx1, y + 16, fill="#2a2a2a", outline="#444444")
        if used_w > 0:
            c.create_rectangle(bx0, y, bx0 + used_w, y + 16, fill=bar_color, outline="")
        y += 26

        # App section
        pkg = d.get('app_pkg', '')
        pss = d.get('app_pss_mb', 0)
        c.create_text(16, y, text="Foreground App", fill=self.FG_DIM,
                      font=("Menlo", 10), anchor="nw")
        y += 18
        if pkg:
            c.create_text(16, y, text=pkg, fill=self.ACCENT,
                          font=("Menlo", 11), anchor="nw")
            y += 18
            pss_str = f"PSS: {pss:,} MB" if pss else "PSS: measuring..."
            c.create_text(16, y, text=pss_str, fill=self.FG,
                          font=("Menlo", 11), anchor="nw")
            y += 22
        else:
            c.create_text(16, y, text="(detecting...)", fill=self.FG_DIM,
                          font=("Menlo", 11), anchor="nw")
            y += 22

        # Sparkline
        hist = list(self._mem_history)
        if len(hist) > 1:
            y += 4
            c.create_text(16, y, text="Used % — last 2 min", fill=self.FG_DIM,
                          font=("Menlo", 10), anchor="nw")
            y += 16
            gx0, gy0 = 16, y
            gx1 = W - 16
            gh = H - y - 8
            if gh < 40:
                gh = 40
            gy1 = gy0 + gh
            c.create_rectangle(gx0, gy0, gx1, gy1, fill="#111111", outline="#333333")
            gw = gx1 - gx0
            for pct_line in (25, 50, 75):
                ly = gy0 + int(gh * (1 - pct_line / 100))
                c.create_line(gx0, ly, gx1, ly, fill="#2a2a2a", dash=(2, 4))
                c.create_text(gx0 + 3, ly - 1, text=f"{pct_line}%",
                              fill="#555555", font=("Menlo", 8), anchor="sw")
            n = len(hist)
            pts = []
            for i, v in enumerate(hist):
                px = gx0 + int(i * gw / max(n - 1, 1))
                py = gy0 + int(gh * (1 - v / 100))
                pts.extend([px, py])
            line_color = "#ff4444" if hist[-1] > 80 else "#ff8c35" if hist[-1] > 60 else self.ACCENT
            c.create_line(pts, fill=line_color, width=2, smooth=True)
