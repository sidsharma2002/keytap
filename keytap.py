#!/usr/bin/env python3
"""
keytap - Android mirror with always-on grid overlay

Controls:
  A1..J8       tap that cell (type row letter then column number)
  Backspace    clear input buffer
  Esc          quit

Usage:
  python3 keytap.py
  python3 keytap.py --serial emulator-5554
  python3 keytap.py --cols 4 --rows 8 --height 800
"""

import subprocess
import sys
import string
import argparse
import tkinter as tk
import threading
import base64
import re
import io
import queue
import time
import os
import shutil

try:
    from PIL import Image, ImageTk, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

ADB_PATH   = "/Users/sidharthsharma/Library/Android/sdk/platform-tools/adb"
CURSOR_MODE = True   # True = arrow-key cursor; False = 2-letter label typing
CURSOR_JUMP = 5      # Shift+arrow jumps this many cells at once
FIFO_PATH  = "/tmp/keytap_stream.fifo"
HAS_SCRCPY = shutil.which("scrcpy") is not None
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--serial", default=None, help="ADB serial (e.g. emulator-5554)")
    p.add_argument("--cols",   type=int, default=10)
    p.add_argument("--rows",   type=int, default=32)
    p.add_argument("--height", type=int, default=800, help="mirror window height px")
    return p.parse_args()


ARGS = parse_args()


def adb(*cmd):
    full = [ADB_PATH]
    if ARGS.serial:
        full += ["-s", ARGS.serial]
    full += list(cmd)
    return subprocess.run(full, capture_output=True, timeout=15)


def get_device_size():
    out = adb("shell", "wm", "size").stdout.decode()
    m = re.search(r'(\d+)x(\d+)', out)
    if not m:
        return 1080, 2400
    w, h = int(m.group(1)), int(m.group(2))
    return (w, h) if w < h else (h, w)


def take_screencap():
    result = adb("exec-out", "screencap", "-p")
    return result.stdout  # raw PNG bytes


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

        # Center over parent
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
        # Search row
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

        # Divider
        tk.Frame(self.win, bg="#3a3a3c", height=1).pack(fill="x")

        # Results
        self._listbox = tk.Listbox(
            self.win, bg=self.BG, fg=self.FG,
            selectbackground=self.SEL_BG, selectforeground=self.ACCENT,
            relief="flat", bd=0, highlightthickness=0,
            font=("Menlo", 12), activestyle="none", height=13
        )
        self._listbox.pack(fill="both", expand=True, padx=8, pady=4)

        # Footer
        self._footer = tk.StringVar()
        tk.Label(self.win, textvariable=self._footer,
                 bg=self.BG, fg=self.FG_DIM, font=("Menlo", 10),
                 anchor="w", padx=14).pack(fill="x", pady=(0, 8))

        # Bindings
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

    def _selected(self):
        cur = self._listbox.curselection()
        if not cur:
            return None
        if self._state == "search":
            return self._filtered[cur[0]]
        return self.ACTIONS[cur[0]][1]

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


class KeyTap:
    def __init__(self, dev_w, dev_h):
        self.dev_w = dev_w
        self.dev_h = dev_h
        self.scale  = ARGS.height / dev_h
        self.win_w  = int(dev_w * self.scale)
        self.win_h  = ARGS.height

        self.input_buf      = []
        self.cursor_row     = 0
        self.cursor_col     = 0
        self.photo          = None
        self._raw_frame     = None
        self._frame_q       = queue.Queue(maxsize=1)
        self._grid_font     = self._load_grid_font()
        self._last_scroll_t = 0.0
        self._last_shift_t  = 0.0
        self._packages      = []
        self._packages_ready = False
        self._palette       = None

        self._build_ui()

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

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _load_grid_font(self):
        if not HAS_PIL:
            return None
        fs = max(8, int(min(self.win_w / ARGS.cols, self.win_h / ARGS.rows) // 5))
        for path in [
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/Monaco.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ]:
            try:
                return ImageFont.truetype(path, fs)
            except Exception:
                pass
        return ImageFont.load_default()

    def _composite_grid(self, frame):
        """Progressive-reveal grid overlay composited onto a PIL Image."""
        base = frame.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        W, H = base.size
        cw = W / ARGS.cols
        ch = H / ARGS.rows
        typed = ''.join(self.input_buf).upper()
        row_selected = len(typed) == 1

        for r in range(ARGS.rows):
            row_letter = string.ascii_uppercase[r] if r < 26 else None
            row_match = row_selected and row_letter and row_letter == typed[0]

            for c in range(ARGS.cols):
                x0, y0 = int(c * cw), int(r * ch)
                x1, y1 = int((c + 1) * cw), int((r + 1) * ch)
                is_cursor = CURSOR_MODE and r == self.cursor_row and c == self.cursor_col
                label = f"{row_letter}{string.ascii_uppercase[c]}" if row_letter else None

                if not typed:
                    draw.rectangle([x0, y0, x1 - 1, y1 - 1],
                                   outline=(255, 255, 255, 38))
                    if label and not CURSOR_MODE:
                        self._draw_pill(draw, x0, y0, label, active=False)
                elif row_match:
                    draw.rectangle([x0, y0, x1, y1], fill=(255, 215, 0, 28))
                    draw.rectangle([x0, y0, x1 - 1, y1 - 1],
                                   outline=(255, 215, 0, 200))
                    if label and not CURSOR_MODE:
                        self._draw_pill(draw, x0, y0, label, active=True)
                else:
                    draw.rectangle([x0, y0, x1 - 1, y1 - 1],
                                   outline=(255, 255, 255, 15))
                    if label and not CURSOR_MODE:
                        self._draw_pill(draw, x0, y0, label, active=None)

                # Cursor cell: green selection border + subtle fill
                if is_cursor:
                    draw.rectangle([x0, y0, x1, y1], fill=(0, 255, 80, 25))
                    draw.rectangle([x0 + 1, y0 + 1, x1 - 2, y1 - 2],
                                   outline=(0, 255, 80, 255), width=2)

        return Image.alpha_composite(base, overlay).convert("RGB")

    def _draw_pill(self, draw, x0, y0, label, active=False):
        """Draw a pill badge in top-left corner. active=True: bright, False: dim, None: ghost."""
        pad = 3
        try:
            bbox = self._grid_font.getbbox(label)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = len(label) * 7, 10
        px0, py0 = x0 + 3, y0 + 3
        px1, py1 = px0 + tw + pad * 2, py0 + th + pad * 2
        if active is True:
            bg    = (0, 0, 0, 200)
            color = (0, 255, 110, 255)
        elif active is False:
            bg    = (0, 0, 0, 140)
            color = (0, 200, 75, 255)
        else:  # ghost: typing but not this row
            bg    = (0, 0, 0, 70)
            color = (0, 150, 55, 255)
        draw.rectangle([px0, py0, px1, py1], fill=bg)
        draw.text((px0 + pad, py0 + pad), label, fill=color, font=self._grid_font)

    def redraw(self):
        if self._raw_frame is not None:
            composited = self._composite_grid(self._raw_frame)
            self.photo = ImageTk.PhotoImage(composited)
        self.canvas.itemconfig(self._img_id, image=self.photo or "")

    # ── Capture pipeline ──────────────────────────────────────────────────────

    def _start_stream(self):
        """Start scrcpy + ffmpeg pipeline. Falls back to screencap if unavailable."""
        if HAS_SCRCPY and HAS_FFMPEG:
            # Show one screencap immediately while scrcpy connects
            threading.Thread(target=self._initial_screencap, daemon=True).start()
            threading.Thread(target=self._scrcpy_stream, daemon=True).start()
        else:
            missing = []
            if not HAS_SCRCPY: missing.append("scrcpy")
            if not HAS_FFMPEG:  missing.append("ffmpeg")
            self.root.after(0, lambda: self.status(f"missing {','.join(missing)} - using screencap"))
            threading.Thread(target=self._screencap_loop, daemon=True).start()

    def _initial_screencap(self):
        """Take one screencap immediately so window isn't blank while scrcpy connects."""
        try:
            png = take_screencap()
            if png and len(png) > 512:
                img = Image.open(io.BytesIO(png))
                img = img.resize((self.win_w, self.win_h), Image.BILINEAR)
                try:
                    self._frame_q.get_nowait()
                except queue.Empty:
                    pass
                self._frame_q.put(img)
        except Exception:
            pass

    def _scrcpy_stream(self):
        """scrcpy → FIFO → ffmpeg → raw RGB24 frames."""
        try:
            os.unlink(FIFO_PATH)
        except FileNotFoundError:
            pass
        os.mkfifo(FIFO_PATH)

        scrcpy_cmd = ["scrcpy",
                      "--record", FIFO_PATH,
                      "--record-format=mkv",
                      "--no-video-playback",
                      "--no-audio"]
        if ARGS.serial:
            scrcpy_cmd += [f"--serial={ARGS.serial}"]

        ffmpeg_cmd = [
            "ffmpeg",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "matroska", "-i", FIFO_PATH,
            "-vf", f"scale={self.win_w}:{self.win_h}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"
        ]

        t_start = time.time()
        # Start both concurrently - FIFO open() blocks until both ends are open,
        # no sleep needed; OS synchronizes reader/writer handshake.
        scrcpy_proc = subprocess.Popen(scrcpy_cmd, stderr=subprocess.DEVNULL)
        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        self._scrcpy_proc = scrcpy_proc
        self._ffmpeg_proc = ffmpeg_proc
        self.root.after(0, lambda: self.status("connecting scrcpy..."))

        frame_size = self.win_w * self.win_h * 3
        first_frame = True
        while True:
            data = ffmpeg_proc.stdout.read(frame_size)
            if len(data) != frame_size:
                self.root.after(0, lambda: self.status("stream ended - restarting..."))
                ffmpeg_proc.wait()
                scrcpy_proc.wait()
                time.sleep(1)
                threading.Thread(target=self._scrcpy_stream, daemon=True).start()
                return
            if first_frame:
                elapsed = time.time() - t_start
                print(f"[keytap] first scrcpy frame in {elapsed:.2f}s", flush=True)
                self.root.after(0, lambda e=elapsed: self.status(f"streaming  |  first frame in {e:.2f}s"))
                first_frame = False
            img = Image.frombytes("RGB", (self.win_w, self.win_h), data)
            try:
                self._frame_q.get_nowait()
            except queue.Empty:
                pass
            self._frame_q.put(img)

    def _screencap_loop(self):
        """Fallback: ADB screencap loop."""
        while True:
            try:
                png = take_screencap()
                if png and len(png) > 512:
                    if HAS_PIL:
                        img = Image.open(io.BytesIO(png))
                        img = img.resize((self.win_w, self.win_h), Image.BILINEAR)
                        frame = img
                    else:
                        frame = base64.b64encode(png).decode()
                    try:
                        self._frame_q.get_nowait()
                    except queue.Empty:
                        pass
                    self._frame_q.put(frame)
                else:
                    time.sleep(0.5)
            except Exception as e:
                self.root.after(0, lambda e=e: self.status(f"capture error: {e}"))
                time.sleep(0.5)

    def _display_loop(self):
        """Main thread: poll queue at ~60fps, update canvas only when new frame ready."""
        try:
            frame = self._frame_q.get_nowait()
            self._raw_frame = frame
            composited = self._composite_grid(frame)
            self.photo = ImageTk.PhotoImage(composited)
            self.canvas.itemconfig(self._img_id, image=self.photo)
            self._idle_status()
        except queue.Empty:
            pass
        self.root.after(16, self._display_loop)

    # ── ADB actions ──────────────────────────────────────────────────────────

    def do_tap(self, dx, dy):
        self.status(f"tap ({dx},{dy})")
        threading.Thread(
            target=lambda: adb("shell", "input", "tap", str(dx), str(dy)),
            daemon=True
        ).start()

    def do_keyevent(self, code, label):
        self.status(f"keyevent: {label}")
        threading.Thread(
            target=lambda: adb("shell", "input", "keyevent", str(code)),
            daemon=True
        ).start()

    def do_swipe(self, x1, y1, x2, y2, duration=300, label="swipe"):
        self.status(f"{label} ({x1},{y1})→({x2},{y2})")
        threading.Thread(
            target=lambda: adb("shell", "input", "swipe",
                               str(x1), str(y1), str(x2), str(y2), str(duration)),
            daemon=True
        ).start()

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self, msg):
        self.status_var.set(msg)

    def _idle_status(self):
        if CURSOR_MODE:
            r, c = self.cursor_row, self.cursor_col
            row_label = string.ascii_uppercase[r] if r < 26 else str(r + 1)
            col_label = string.ascii_uppercase[c]
            self.status(f"cursor: {row_label}{col_label} (row {r+1}, col {c+1})  |  arrows=move  Shift=jump5  space=tap  w/s=scroll  b=back  h=home  r=recents  Esc=quit")
        else:
            buf = ''.join(self.input_buf).upper()
            cols_range = string.ascii_uppercase[:ARGS.cols]
            rows_range = string.ascii_uppercase[:min(ARGS.rows, 26)]
            if len(buf) == 1:
                self.status(f"row {buf} selected  →  type col ({cols_range[0]}-{cols_range[-1]})  |  Backspace=cancel")
            else:
                self.status(f"type row ({rows_range[0]}-{rows_range[-1]}) then col ({cols_range[0]}-{cols_range[-1]})  |  Esc=quit")

    # ── Key handler ──────────────────────────────────────────────────────────

    def on_key(self, event):
        sym  = event.keysym
        char = event.char.upper() if event.char else ""

        if sym == 'Escape':
            self.root.destroy()
            return

        # Double Shift → open command palette
        if sym in ('Shift_L', 'Shift_R'):
            now = time.time()
            if now - self._last_shift_t < 0.4:
                self._open_palette()
                self._last_shift_t = 0.0
            else:
                self._last_shift_t = now
            return

        if CURSOR_MODE:
            shift = bool(event.state & 0x1)
            step  = CURSOR_JUMP if shift else 1
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
                self.do_keyevent(4, "back")
                return
            elif char == 'H':
                self.do_keyevent(3, "home")
                return
            elif char == 'R':
                self.do_keyevent(187, "recents")
                return
            elif char in ('W', 'S'):
                now = time.time()
                if now - self._last_scroll_t < 0.4:
                    return  # throttle: swipe still in flight, ignore repeat
                self._last_scroll_t = now
                cx, cy = self.cell_to_dev_rc(self.cursor_row, self.cursor_col)
                dist = int(self.dev_h * 0.4)
                if char == 'W':
                    # scroll up: drag finger down → reveals items above
                    y1 = max(cy - dist // 2, 0)
                    y2 = min(cy + dist // 2, self.dev_h - 1)
                    self.do_swipe(cx, y1, cx, y2, label="scroll up")
                else:
                    # scroll down: drag finger up → reveals items below
                    y1 = min(cy + dist // 2, self.dev_h - 1)
                    y2 = max(cy - dist // 2, 0)
                    self.do_swipe(cx, y1, cx, y2, label="scroll down")
                return
            else:
                return
            self.redraw()
            self._idle_status()
            return

        # 2-letter label mode (active when CURSOR_MODE = False)
        if sym == 'BackSpace':
            if self.input_buf:
                self.input_buf.pop()
                self.redraw()
                self._idle_status()
            return

        if char.isalpha() and not self.input_buf:
            self.input_buf.append(char)
            self.redraw()
            self._idle_status()
            return

        if char.isalpha() and len(self.input_buf) == 1:
            self.input_buf.append(char)
            label = ''.join(self.input_buf)
            self.input_buf.clear()
            try:
                dx, dy = self.cell_to_dev(label)
                self.do_tap(dx, dy)
            except ValueError:
                self.status(f"'{label}' out of range")
            self.redraw()
            return

    # ── Command palette ──────────────────────────────────────────────────────

    def _fetch_packages(self):
        result = adb("shell", "pm", "list", "packages")
        pkgs = []
        for line in result.stdout.decode().strip().splitlines():
            line = line.strip()
            if line.startswith("package:"):
                pkgs.append(line[len("package:"):])
        self._packages = sorted(pkgs)
        self._packages_ready = True

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
            on_action=self._on_palette_action
        )

    def _on_palette_action(self, pkg, action):
        if action == "launch":
            self.status(f"launch {pkg}")
            threading.Thread(
                target=lambda: adb("shell", "monkey", "-p", pkg,
                                   "-c", "android.intent.category.LAUNCHER", "1"),
                daemon=True
            ).start()
        elif action == "force-stop":
            self.status(f"force-stop {pkg}")
            threading.Thread(
                target=lambda: adb("shell", "am", "force-stop", pkg),
                daemon=True
            ).start()
        elif action == "clear-data":
            self.status(f"clear-data {pkg}")
            threading.Thread(
                target=lambda: adb("shell", "pm", "clear", pkg),
                daemon=True
            ).start()
        elif action == "uninstall":
            self.status(f"uninstall {pkg}")
            threading.Thread(
                target=lambda: adb("shell", "pm", "uninstall", pkg),
                daemon=True
            ).start()

    # ── Run ──────────────────────────────────────────────────────────────────

    def run(self):
        threading.Thread(target=self._fetch_packages, daemon=True).start()
        self._start_stream()
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


if __name__ == "__main__":
    main()
