import sys
import string
import tkinter as tk
from PIL import ImageTk
import threading
import time

from config import ARGS, CURSOR_MODE, CURSOR_JUMP
from adb import adb, get_device_size
from capture import CaptureManager
from grid import GridRenderer
from palette import CommandPalette


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
        self._elements        = []
        self._element_mode    = False
        self._element_loading = False

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
            win_elements = [
                {**el,
                 'wx1': int(el['x1'] * s), 'wy1': int(el['y1'] * s),
                 'wx2': int(el['x2'] * s), 'wy2': int(el['y2'] * s)}
                for el in self._elements
            ]
        composited = self._renderer.composite(
            frame, self.cursor_row, self.cursor_col, self.input_buf, win_elements
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
        except Exception:
            pass
        self.root.after(16, self._display_loop)

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self, msg):
        self.status_var.set(msg)

    def _idle_status(self):
        if self._element_mode:
            n = len(self._elements)
            self.status(
                f"element mode: {n} elements  |  a-z=tap element  arrows=move  e=exit"
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

        # Element mode: letters select elements, arrows still move cursor
        if self._element_mode:
            moved = False
            if sym == 'Up':    self.cursor_row = max(0, self.cursor_row - step); moved = True
            elif sym == 'Down':  self.cursor_row = min(ARGS.rows - 1, self.cursor_row + step); moved = True
            elif sym == 'Left':  self.cursor_col = max(0, self.cursor_col - step); moved = True
            elif sym == 'Right': self.cursor_col = min(ARGS.cols - 1, self.cursor_col + step); moved = True
            elif char and char.isalpha():
                self._tap_element(char.lower())
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
        self.redraw()
        self._idle_status()

    def _on_element_error(self, msg):
        self._element_loading = False
        self.status(f"element dump failed: {msg}")

    def _exit_element_mode(self):
        self._elements = []
        self._element_mode = False
        self.redraw()
        self._idle_status()

    def _tap_element(self, char):
        for el in self._elements:
            if el['label'] == char:
                self.do_tap(el['cx'], el['cy'])
                hint = el['text'] or el['resource_id'] or el['label']
                self.status(f"tap '{hint}' ({el['cx']},{el['cy']})")
                return
        self.status(f"no element '{char}'")

    # ── Command palette ──────────────────────────────────────────────────────

    def _fetch_packages(self):
        result = adb("shell", "pm", "list", "packages")
        self._packages = sorted(
            line.strip()[len("package:"):]
            for line in result.stdout.decode().splitlines()
            if line.strip().startswith("package:")
        )
        self._packages_ready = True

    def _open_palette(self):
        if self._palette and self._palette.win.winfo_exists():
            self._palette.win.lift()
            self._palette.win.focus_force()
            return
        if not self._packages_ready:
            self.status("package list still loading...")
            return
        self._palette = CommandPalette(self.root, self._packages,
                                       on_action=self._on_palette_action)

    def _on_palette_action(self, pkg, action):
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

    # ── Run ──────────────────────────────────────────────────────────────────

    def run(self):
        threading.Thread(target=self._fetch_packages, daemon=True).start()
        self._capture.start()
        self._display_loop()
        self.root.mainloop()


def main():
    print("connecting...")
    try:
        dev_w, dev_h = get_device_size()
    except Exception as e:
        print(f"ADB error: {e}")
        print("Is device connected? Run: adb devices")
        sys.exit(1)

    print(f"device {dev_w}x{dev_h} | mirror {int(dev_w * ARGS.height / dev_h)}x{ARGS.height}")
    KeyTap(dev_w, dev_h).run()
