import sys
import os
import json
import string
import subprocess
import tkinter as tk
from PIL import ImageTk, ImageChops
import threading
import time

from config import ARGS, CURSOR_MODE, CURSOR_JUMP
from adb import adb, get_device_size
from capture import CaptureManager
from grid import GridRenderer
from palette import CommandPalette
from elements import LABEL_CHARS
from inspectors import shared_prefs, remote_config, litmus, permissions, dev_options
from inspectors.hierarchy import dump_hierarchy_tree


class KeyTap:
    def __init__(self, dev_w, dev_h):
        self.dev_w = dev_w
        self.dev_h = dev_h
        self.scale  = ARGS.height / dev_h
        self.win_w  = int(dev_w * self.scale)
        self.win_h  = ARGS.height

        self.input_buf        = []
        self.cursor_row       = 0
        self.cursor_col       = 0
        self.photo            = None
        self._raw_frame       = None
        self._last_scroll_t   = 0.0
        self._last_shift_t    = 0.0
        self._packages        = []
        self._packages_ready  = False
        self._palette         = None
        self._deeplink_history = []
        self._elements        = []
        self._element_mode    = False
        self._element_loading = False
        self._element_buf     = []
        self._elem_ref_frame   = None
        self._elem_last_dump_t = 0.0
        self._elem_show_bounds = False
        self._hier_highlight   = None   # (wx1, wy1, wx2, wy2) or None

        self._build_ui()

        self._capture  = CaptureManager(
            self.win_w, self.win_h,
            on_status=lambda msg: self.root.after(0, lambda m=msg: self.status(m))
        )
        self._renderer = GridRenderer(self.win_w, self.win_h)

    # ── UI setup ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("keytap")
        self.root.resizable(False, False)
        self.root.configure(bg="#111")

        self.canvas = tk.Canvas(
            self.root, width=self.win_w, height=self.win_h,
            bg="black", highlightthickness=0, cursor="none"
        )
        self.canvas.pack()
        self._img_id = self.canvas.create_image(0, 0, anchor="nw")

        self.status_var = tk.StringVar(value="connecting...")
        status_frame = tk.Frame(self.root, bg="#1a1a1a", width=self.win_w, height=22)
        status_frame.pack(fill="x")
        status_frame.pack_propagate(False)
        tk.Label(
            status_frame, textvariable=self.status_var,
            bg="#1a1a1a", fg="#888", font=("Menlo", 11), anchor="w", padx=8
        ).pack(fill="both", expand=True)

        self.root.bind("<KeyPress>", self.on_key)
        self.root.focus_force()

    # ── Coordinate helpers ────────────────────────────────────────────────────

    def cell_to_dev(self, label):
        r = string.ascii_uppercase.index(label[0].upper())
        c = string.ascii_uppercase.index(label[1].upper())
        if r >= ARGS.rows or c >= ARGS.cols:
            raise ValueError("out of range")
        return self.cell_to_dev_rc(r, c)

    def cell_to_dev_rc(self, r, c):
        cw = self.dev_w / ARGS.cols
        ch = self.dev_h / ARGS.rows
        return int(c * cw + cw / 2), int(r * ch + ch / 2)

    # ── Display ───────────────────────────────────────────────────────────────

    def _push_frame(self, frame):
        win_elements = None
        if self._element_mode and self._elements:
            s = self.scale
            buf = self._element_buf
            win_elements = []
            for el in self._elements:
                if not buf:
                    display_label, active = el['label'], True
                elif el['label'][0] == buf[0]:
                    display_label, active = el['label'], True
                else:
                    display_label, active = el['label'], False
                win_elements.append({
                    **el,
                    'wx1': int(el['x1'] * s), 'wy1': int(el['y1'] * s),
                    'wx2': int(el['x2'] * s), 'wy2': int(el['y2'] * s),
                    'display_label': display_label,
                    'active': active,
                    'show_bounds': self._elem_show_bounds,
                })
        composited = self._renderer.composite(
            frame, self.cursor_row, self.cursor_col, self.input_buf, win_elements,
            highlight_bounds=self._hier_highlight
        )
        self.photo = ImageTk.PhotoImage(composited)
        self.canvas.itemconfig(self._img_id, image=self.photo)

    def redraw(self):
        if self._raw_frame is not None:
            self._push_frame(self._raw_frame)

    def _display_loop(self):
        try:
            frame = self._capture.frame_q.get_nowait()
            self._raw_frame = frame
            self._push_frame(frame)
            self._idle_status()
            if self._element_mode and not self._element_loading:
                self._check_screen_change(frame)
        except Exception:
            pass
        self.root.after(16, self._display_loop)

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self, msg):
        self.status_var.set(msg)

    def _idle_status(self):
        if self._element_mode:
            n = len(self._elements)
            bounds_hint = "Tab=show bounds" if not self._elem_show_bounds else "Tab=hide bounds"
            self.status(
                f"element mode: {n} elements  |  type 2-char label to tap  {bounds_hint}  e=exit"
            )
            return
        if CURSOR_MODE:
            r, c = self.cursor_row, self.cursor_col
            row_label = string.ascii_uppercase[r] if r < 26 else str(r + 1)
            col_label = string.ascii_uppercase[c]
            self.status(
                f"cursor: {row_label}{col_label} (row {r+1}, col {c+1})"
                f"  |  arrows=move  Shift=jump5  space=tap"
                f"  w/s=scroll  b=back  h=home  r=recents  Esc=quit"
            )
        else:
            buf = ''.join(self.input_buf).upper()
            cols_range = string.ascii_uppercase[:ARGS.cols]
            rows_range = string.ascii_uppercase[:min(ARGS.rows, 26)]
            if len(buf) == 1:
                self.status(f"row {buf} selected  ->  type col ({cols_range[0]}-{cols_range[-1]})  |  Backspace=cancel")
            else:
                self.status(f"type row ({rows_range[0]}-{rows_range[-1]}) then col ({cols_range[0]}-{cols_range[-1]})  |  Esc=quit")

    # ── ADB actions ──────────────────────────────────────────────────────────

    def _run_adb(self, *cmd):
        threading.Thread(target=lambda: adb(*cmd), daemon=True).start()

    def do_tap(self, dx, dy):
        self.status(f"tap ({dx},{dy})")
        self._run_adb("shell", "input", "tap", str(dx), str(dy))

    def do_keyevent(self, code, label):
        self.status(f"keyevent: {label}")
        self._run_adb("shell", "input", "keyevent", str(code))

    def do_swipe(self, x1, y1, x2, y2, duration=300, label="swipe"):
        self.status(f"{label} ({x1},{y1})->({x2},{y2})")
        self._run_adb("shell", "input", "swipe",
                      str(x1), str(y1), str(x2), str(y2), str(duration))

    # ── Key handler ──────────────────────────────────────────────────────────

    def _do_scroll(self, char):
        now = time.time()
        if now - self._last_scroll_t < 0.4:
            return
        self._last_scroll_t = now
        cx, cy = self.cell_to_dev_rc(self.cursor_row, self.cursor_col)
        dist = int(self.dev_h * 0.4)
        if char == 'W':
            self.do_swipe(cx, max(cy - dist // 2, 0),
                          cx, min(cy + dist // 2, self.dev_h - 1), label="scroll up")
        else:
            self.do_swipe(cx, min(cy + dist // 2, self.dev_h - 1),
                          cx, max(cy - dist // 2, 0), label="scroll down")

    def _handle_cursor_key(self, event):
        sym  = event.keysym
        char = event.char.upper() if event.char else ""
        step = CURSOR_JUMP if bool(event.state & 0x1) else 1

        # E: toggle element overlay (takes priority always)
        if char == 'E':
            self._exit_element_mode() if self._element_mode else self._enter_element_mode()
            return

        # Element mode: 2-char label selection; system shortcuts still work
        if self._element_mode:
            moved = False
            if sym == 'Tab':
                self._elem_show_bounds = not self._elem_show_bounds
                self.redraw()
            elif sym == 'BackSpace':
                if self._element_buf:
                    self._element_buf.clear()
                    self.redraw()
                    self._idle_status()
            elif sym == 'Up':      self.cursor_row = max(0, self.cursor_row - step); moved = True
            elif sym == 'Down':    self.cursor_row = min(ARGS.rows - 1, self.cursor_row + step); moved = True
            elif sym == 'Left':    self.cursor_col = max(0, self.cursor_col - step); moved = True
            elif sym == 'Right':   self.cursor_col = min(ARGS.cols - 1, self.cursor_col + step); moved = True
            elif sym == 'space':
                dx, dy = self.cell_to_dev_rc(self.cursor_row, self.cursor_col)
                self.do_tap(dx, dy)
            elif char == 'B': self.do_keyevent(4, "back")
            elif char == 'H': self.do_keyevent(3, "home")
            elif char == 'R': self.do_keyevent(187, "recents")
            elif char in ('W', 'S'): self._do_scroll(char)
            elif char and char.lower() in LABEL_CHARS:
                self._handle_element_input(char.lower())
            if moved:
                self.redraw()
                self._idle_status()
            return

        if sym == 'Up':
            self.cursor_row = max(0, self.cursor_row - step)
        elif sym == 'Down':
            self.cursor_row = min(ARGS.rows - 1, self.cursor_row + step)
        elif sym == 'Left':
            self.cursor_col = max(0, self.cursor_col - step)
        elif sym == 'Right':
            self.cursor_col = min(ARGS.cols - 1, self.cursor_col + step)
        elif sym == 'space':
            dx, dy = self.cell_to_dev_rc(self.cursor_row, self.cursor_col)
            self.do_tap(dx, dy)
            return
        elif char == 'B':
            self.do_keyevent(4, "back"); return
        elif char == 'H':
            self.do_keyevent(3, "home"); return
        elif char == 'R':
            self.do_keyevent(187, "recents"); return
        elif char in ('W', 'S'):
            self._do_scroll(char); return
        else:
            return
        self.redraw()
        self._idle_status()

    def _handle_label_key(self, sym, char):
        if sym == 'BackSpace':
            if self.input_buf:
                self.input_buf.pop()
                self.redraw()
                self._idle_status()
            return
        if not char.isalpha():
            return
        self.input_buf.append(char)
        if len(self.input_buf) == 1:
            self.redraw()
            self._idle_status()
            return
        label = ''.join(self.input_buf)
        self.input_buf.clear()
        try:
            dx, dy = self.cell_to_dev(label)
            self.do_tap(dx, dy)
        except ValueError:
            self.status(f"'{label}' out of range")
        self.redraw()

    def on_key(self, event):
        sym = event.keysym

        if sym == 'Escape':
            self.root.destroy()
            return

        if sym in ('Shift_L', 'Shift_R'):
            now = time.time()
            if now - self._last_shift_t < 0.4:
                self._open_palette()
                self._last_shift_t = 0.0
            else:
                self._last_shift_t = now
            return

        if CURSOR_MODE:
            self._handle_cursor_key(event)
        else:
            self._handle_label_key(sym, event.char.upper() if event.char else "")

    # ── Element overlay ──────────────────────────────────────────────────────

    def _enter_element_mode(self):
        if self._element_loading:
            return
        self._element_loading = True
        if not self._element_mode:
            self.status("dumping UI hierarchy...")
        threading.Thread(target=self._fetch_elements, daemon=True).start()

    def _fetch_elements(self):
        try:
            from elements import dump_elements
            els = dump_elements(ARGS.serial)
            self.root.after(0, lambda e=els: self._on_elements_loaded(e))
        except Exception as ex:
            self.root.after(0, lambda m=str(ex): self._on_element_error(m))

    def _on_elements_loaded(self, elements):
        self._element_loading = False
        self._elements = elements
        self._element_mode = True
        self._elem_ref_frame = self._raw_frame
        self._elem_last_dump_t = time.time()
        self._element_buf.clear()
        self.redraw()
        self._idle_status()

    def _check_screen_change(self, frame):
        if self._elem_ref_frame is None:
            return
        if time.time() - self._elem_last_dump_t < 0.5:
            return
        small = frame.resize((90, 200))
        ref   = self._elem_ref_frame.resize((90, 200))
        diff  = ImageChops.difference(small, ref)
        bbox  = diff.getbbox()
        if bbox and (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) > 90 * 200 * 0.08:
            self._enter_element_mode()

    def _on_element_error(self, msg):
        self._element_loading = False
        self.status(f"element dump failed: {msg}")

    def _exit_element_mode(self):
        self._elements = []
        self._element_buf.clear()
        self._element_mode = False
        self._elem_show_bounds = False
        self.redraw()
        self._idle_status()

    def _handle_element_input(self, char):
        self._element_buf.append(char)
        if len(self._element_buf) == 1:
            matches = [el for el in self._elements if el['label'][0] == char]
            if not matches:
                self.status(f"no element '{char}...'")
                self._element_buf.clear()
                return
            self.redraw()
            self.status(f"'{char}...' - press second key  |  Backspace=cancel")
        elif len(self._element_buf) == 2:
            label = ''.join(self._element_buf)
            self._element_buf.clear()
            el = next((e for e in self._elements if e['label'] == label), None)
            if el:
                hint = el['text'] or el['resource_id'] or label
                self.do_tap(el['cx'], el['cy'])
                self.status(f"tap '{hint}' ({el['cx']},{el['cy']})")
            else:
                self.status(f"no element '{label}'")
            self.redraw()

    # ── Command palette ──────────────────────────────────────────────────────

    def _fetch_packages(self):
        result = adb("shell", "pm", "list", "packages")
        self._packages = sorted(
            line.strip()[len("package:"):]
            for line in result.stdout.decode().splitlines()
            if line.strip().startswith("package:")
        )
        self._packages_ready = True

    _DEEPLINK_HISTORY_PATH = os.path.expanduser("~/.keytap_deeplinks.json")

    def _load_deeplink_history(self):
        try:
            with open(self._DEEPLINK_HISTORY_PATH) as f:
                self._deeplink_history = json.load(f)
        except Exception:
            self._deeplink_history = []

    def _save_deeplink_history(self):
        try:
            with open(self._DEEPLINK_HISTORY_PATH, "w") as f:
                json.dump(self._deeplink_history, f)
        except Exception:
            pass

    def _read_clipboard(self):
        try:
            return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=1).stdout
        except Exception:
            return ""

    def _open_palette(self):
        if self._palette and self._palette.win.winfo_exists():
            self._palette.win.lift()
            self._palette.win.focus_force()
            return
        if not self._packages_ready:
            self.status("package list still loading...")
            return
        self._palette = CommandPalette(
            self.root, self._packages,
            on_action=self._on_palette_action,
            clipboard_text=self._read_clipboard(),
            deeplink_history=self._deeplink_history,
            serial=ARGS.serial,
        )

    def _on_palette_action(self, pkg, action):
        if pkg == "__set-theme__":
            self.root.after(150, self._open_palette)
            return
        if pkg == "__dev-options__":
            threading.Thread(target=self._fetch_dev_options, daemon=True).start()
            return
        if pkg == "__quick-toggle__":
            threading.Thread(target=lambda k=action: self._do_quick_toggle(k), daemon=True).start()
            return
        if pkg == "__view-hierarchy__":
            threading.Thread(target=self._fetch_hierarchy, daemon=True).start()
            return
        if pkg == "__tap-hierarchy__":
            cx, cy = action.split(",")
            self._run_adb("shell", "input", "tap", cx, cy)
            self.status(f"tapped ({cx},{cy})")
            return
        if pkg == "__refresh-hierarchy__":
            self.status("refreshing hierarchy...")
            threading.Thread(target=self._fetch_hierarchy, daemon=True).start()
            return
        if pkg == "__input-text__":
            text = action
            if not text:
                return
            self.status("input text")
            self._run_adb("shell", "input", "text", text.replace(" ", "%s"))
            return
        if pkg == "__clipboard__":
            text = action  # action holds the clipboard string
            if not text:
                self.status("clipboard empty")
                return
            self.status("paste clipboard")
            self._run_adb("shell", "input", "text", text.replace(" ", "%s"))
            return
        if pkg == "__deeplink__":
            url = action
            self.status(f"launch {url}")
            self._run_adb("shell", "am", "start", "-a",
                          "android.intent.action.VIEW", "-d", url)
            if url in self._deeplink_history:
                self._deeplink_history.remove(url)
            self._deeplink_history.insert(0, url)
            self._deeplink_history = self._deeplink_history[:20]
            threading.Thread(target=self._save_deeplink_history, daemon=True).start()
            return
        self.status(f"{action} {pkg}")
        if action == "launch":
            self._run_adb("shell", "monkey", "-p", pkg,
                          "-c", "android.intent.category.LAUNCHER", "1")
        elif action == "force-stop":
            self._run_adb("shell", "am", "force-stop", pkg)
        elif action == "clear-data":
            self._run_adb("shell", "pm", "clear", pkg)
        elif action == "uninstall":
            self._run_adb("shell", "pm", "uninstall", pkg)
        elif action == "shared-prefs":
            self.status(f"reading shared prefs {pkg}...")
            threading.Thread(target=lambda p=pkg: self._fetch_shared_prefs(p), daemon=True).start()
        elif action == "remote-config":
            self.status(f"reading remote config {pkg}...")
            threading.Thread(target=lambda p=pkg: self._fetch_remote_config(p), daemon=True).start()
        elif action == "litmus":
            self.status(f"reading litmus {pkg}...")
            threading.Thread(target=lambda p=pkg: self._fetch_litmus(p), daemon=True).start()
        elif action == "permissions":
            self.status(f"reading permissions {pkg}...")
            threading.Thread(target=lambda p=pkg: self._fetch_permissions(p), daemon=True).start()

    # ── Inspector fetchers (delegate to inspectors package) ───────────────────

    _QUICK_TOGGLE_LABELS = {
        "wifi":        "WiFi",
        "dark_mode":   "Dark Mode",
        "mobile_data": "Mobile Data",
    }

    def _do_quick_toggle(self, key):
        label = self._QUICK_TOGGLE_LABELS.get(key, key)
        new_state = dev_options.toggle_by_label(label, ARGS.serial)
        self.root.after(0, lambda: self.status(f"{label}: {new_state}"))

    def _fetch_dev_options(self):
        items = dev_options.fetch(ARGS.serial)
        def on_select(label, _state):
            self.status(f"toggling {label}...")
            threading.Thread(target=lambda: self._toggle_dev_option(label), daemon=True).start()
        self.root.after(0, lambda: self._on_viewer_loaded("Dev Options", items, on_select=on_select))

    def _toggle_dev_option(self, label):
        dev_options.toggle_by_label(label, ARGS.serial)
        items = dev_options.fetch(ARGS.serial)
        self.root.after(0, lambda i=items: self._on_dev_options_refresh(i))

    def _on_dev_options_refresh(self, items):
        if self._palette and self._palette.win.winfo_exists():
            self._palette.update_viewer_items(items)

    def _fetch_shared_prefs(self, pkg):
        items = shared_prefs.fetch(pkg)
        self.root.after(0, lambda: self._on_viewer_loaded("Shared Prefs", items))

    def _fetch_remote_config(self, pkg):
        items = remote_config.fetch(pkg)
        self.root.after(0, lambda: self._on_viewer_loaded("Remote Config", items))

    def _fetch_litmus(self, pkg):
        items, raw_by_name = litmus.fetch(pkg)
        def on_select(key, _display):
            litmus.open_nano(key, raw_by_name.get(key, "{}"))
        self.root.after(0, lambda: self._on_viewer_loaded("Litmus", items, on_select=on_select))

    def _fetch_permissions(self, pkg, _refresh=False):
        items = permissions.fetch(pkg)
        def on_select(perm, state):
            if state.startswith("GRANTED"):
                self._run_adb("shell", "pm", "revoke", pkg, perm)
                self.status(f"revoked {perm}")
            else:
                self._run_adb("shell", "pm", "grant", pkg, perm)
                self.status(f"granted {perm}")
            threading.Timer(0.6, lambda p=pkg: self._refresh_permissions(p)).start()
        if _refresh:
            self.root.after(0, lambda i=items: self._on_permissions_refresh(i))
        else:
            self.root.after(0, lambda: self._on_viewer_loaded("Permissions", items, on_select=on_select))

    def _refresh_permissions(self, pkg):
        self._fetch_permissions(pkg, _refresh=True)

    def _on_permissions_refresh(self, items):
        if self._palette and self._palette.win.winfo_exists():
            self._palette.update_viewer_items(items)

    def _fetch_hierarchy(self):
        try:
            root_node, flat_nodes = dump_hierarchy_tree(ARGS.serial)
            self.root.after(0, lambda: self._on_hierarchy_loaded(root_node, flat_nodes))
        except Exception as e:
            self.root.after(0, lambda: self.status(f"hierarchy error: {e}"))

    def _on_hier_hover(self, bounds):
        if bounds:
            s = self.scale
            x1, y1, x2, y2 = bounds
            self._hier_highlight = (int(x1 * s), int(y1 * s), int(x2 * s), int(y2 * s))
        else:
            self._hier_highlight = None
        self.redraw()

    def _on_hierarchy_loaded(self, root_node, flat_nodes):
        self.status(f"hierarchy: {len(flat_nodes)} nodes")
        if self._palette and self._palette.win.winfo_exists():
            self._palette.show_hierarchy(root_node, flat_nodes, on_hover=self._on_hier_hover)

    def _on_viewer_loaded(self, title, items, on_select=None):
        self.status(f"{title}: {len(items)} keys")
        if self._palette and self._palette.win.winfo_exists():
            self._palette.show_viewer(title, items, on_select=on_select)

    # ── Run ──────────────────────────────────────────────────────────────────

    def run(self):
        self._load_deeplink_history()
        threading.Thread(target=self._fetch_packages, daemon=True).start()
        self._capture.start()
        self._display_loop()
        self.root.mainloop()


def main():
    from adb import list_devices

    if not ARGS.serial:
        devices = list_devices()
        if not devices:
            print("No devices connected. Please connect a device and try again.")
            sys.exit(1)
        elif len(devices) == 1:
            ARGS.serial = devices[0]
            print(f"Device: {ARGS.serial}")
        else:
            print("Multiple devices found:")
            for i, serial in enumerate(devices):
                print(f"  [{i + 1}] {serial}")
            while True:
                try:
                    choice = int(input(f"Select device [1-{len(devices)}]: ")) - 1
                    if 0 <= choice < len(devices):
                        ARGS.serial = devices[choice]
                        break
                    print(f"Enter a number between 1 and {len(devices)}")
                except (ValueError, EOFError):
                    print(f"Enter a number between 1 and {len(devices)}")

    print("connecting...")
    try:
        dev_w, dev_h = get_device_size()
    except Exception as e:
        print(f"ADB error: {e}")
        print("Is device connected? Run: adb devices")
        sys.exit(1)

    print(f"device {dev_w}x{dev_h} | mirror {int(dev_w * ARGS.height / dev_h)}x{ARGS.height}")
    KeyTap(dev_w, dev_h).run()
