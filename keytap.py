#!/usr/bin/env python3
"""
keytap - Android mirror with keyboard control
Screencap-based mirror + Vimium-style grid hints + arrow cursor.

Controls:
  f            hint mode (grid labels appear instantly)
  A1..J4       tap that cell
  Esc          cancel hint / quit
  arrows       move cursor (Shift=fast, Option=fine)
  Space        tap at cursor
  s + arrow    swipe direction
  r            refresh screencap

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
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

ADB_PATH   = "/Users/sidharthsharma/Library/Android/sdk/platform-tools/adb"
FIFO_PATH  = "/tmp/keytap_stream.fifo"
HAS_SCRCPY = shutil.which("scrcpy") is not None
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--serial", default=None, help="ADB serial (e.g. emulator-5554)")
    p.add_argument("--cols",   type=int, default=4)
    p.add_argument("--rows",   type=int, default=8)
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


class KeyTap:
    STEP_NORMAL = 50
    STEP_FAST   = 200
    STEP_FINE   = 10
    SWIPE_DIST  = 700
    SWIPE_MS    = 300

    def __init__(self, dev_w, dev_h):
        self.dev_w = dev_w
        self.dev_h = dev_h
        self.scale  = ARGS.height / dev_h
        self.win_w  = int(dev_w * self.scale)
        self.win_h  = ARGS.height

        self.cursor_x = dev_w // 2
        self.cursor_y = dev_h // 2

        self.hint_mode  = False
        self.hint_buf   = []
        self.swipe_mode = False
        self.photo      = None
        self._frame_q   = queue.Queue(maxsize=1)

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

    def to_win(self, dx, dy):
        return int(dx * self.scale), int(dy * self.scale)

    def cell_to_dev(self, label):
        r = string.ascii_uppercase.index(label[0])
        c = int(label[1]) - 1
        if r >= ARGS.rows or c >= ARGS.cols:
            raise ValueError("out of range")
        cw = self.dev_w / ARGS.cols
        ch = self.dev_h / ARGS.rows
        return int(c * cw + cw / 2), int(r * ch + ch / 2)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def redraw(self):
        self.canvas.itemconfig(self._img_id, image=self.photo or "")
        self.canvas.delete("overlay")
        if self.hint_mode:
            self._draw_hints()
        self._draw_cursor()

    def _draw_cursor(self):
        cx, cy = self.to_win(self.cursor_x, self.cursor_y)
        color = "#FF4444" if self.hint_mode else "#00FF88"
        arm = 14
        t = "overlay"
        self.canvas.create_line(cx - arm, cy, cx + arm, cy, fill="black", width=3, tags=t)
        self.canvas.create_line(cx, cy - arm, cx, cy + arm, fill="black", width=3, tags=t)
        self.canvas.create_line(cx - arm, cy, cx + arm, cy, fill=color,   width=1, tags=t)
        self.canvas.create_line(cx, cy - arm, cx, cy + arm, fill=color,   width=1, tags=t)
        self.canvas.create_oval(cx-3, cy-3, cx+3, cy+3, fill=color, outline="", tags=t)

    def _draw_hints(self):
        cw = self.win_w / ARGS.cols
        ch = self.win_h / ARGS.rows
        fs = max(9, int(min(cw, ch) // 4))
        typed = ''.join(self.hint_buf).upper()

        for r in range(ARGS.rows):
            for c in range(ARGS.cols):
                x0, y0 = c * cw, r * ch
                x1, y1 = x0 + cw, y0 + ch
                cx, cy = x0 + cw / 2, y0 + ch / 2
                label = f"{string.ascii_uppercase[r]}{c + 1}"

                active = typed and label.startswith(typed)
                line_color  = "#FFD700" if active else "#FFFFFF"
                label_color = "#FFD700"

                self.canvas.create_rectangle(
                    x0, y0, x1, y1,
                    fill="", outline=line_color, width=1, tags="overlay"
                )
                self.canvas.create_text(cx+1, cy+1, text=label,
                    fill="black", font=("Menlo", fs, "bold"), tags="overlay")
                self.canvas.create_text(cx, cy, text=label,
                    fill=label_color, font=("Menlo", fs, "bold"), tags="overlay")

    # ── Capture pipeline ──────────────────────────────────────────────────────

    def _start_stream(self):
        """Start scrcpy + ffmpeg pipeline. Falls back to screencap if unavailable."""
        if HAS_SCRCPY and HAS_FFMPEG:
            threading.Thread(target=self._scrcpy_stream, daemon=True).start()
        else:
            missing = []
            if not HAS_SCRCPY: missing.append("scrcpy")
            if not HAS_FFMPEG:  missing.append("ffmpeg")
            self.root.after(0, lambda: self.status(f"missing {','.join(missing)} - using screencap"))
            threading.Thread(target=self._screencap_loop, daemon=True).start()

    def _scrcpy_stream(self):
        """scrcpy → FIFO → ffmpeg → raw RGB24 frames."""
        # Setup FIFO
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
            "ffmpeg", "-f", "matroska", "-i", FIFO_PATH,
            "-vf", f"scale={self.win_w}:{self.win_h}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"
        ]

        scrcpy_proc = subprocess.Popen(scrcpy_cmd, stderr=subprocess.DEVNULL)
        # Small delay so scrcpy connects before ffmpeg opens FIFO
        time.sleep(1.5)
        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        self._scrcpy_proc = scrcpy_proc
        self._ffmpeg_proc = ffmpeg_proc
        self.root.after(0, lambda: self.status("streaming via scrcpy..."))

        frame_size = self.win_w * self.win_h * 3
        while True:
            data = ffmpeg_proc.stdout.read(frame_size)
            if len(data) != frame_size:
                self.root.after(0, lambda: self.status("stream ended - restarting..."))
                ffmpeg_proc.wait()
                scrcpy_proc.wait()
                time.sleep(1)
                threading.Thread(target=self._scrcpy_stream, daemon=True).start()
                return
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
            if isinstance(frame, Image.Image):
                self.photo = ImageTk.PhotoImage(frame)
            elif HAS_PIL:
                self.photo = ImageTk.PhotoImage(frame)
            else:
                self.photo = tk.PhotoImage(data=frame)
            self.canvas.itemconfig(self._img_id, image=self.photo)
            self.canvas.delete("overlay")
            if self.hint_mode:
                self._draw_hints()
            self._draw_cursor()
            self._idle_status()
        except queue.Empty:
            pass
        self.root.after(16, self._display_loop)

    # ── ADB actions ──────────────────────────────────────────────────────────

    def do_tap(self, dx, dy):
        self.cursor_x, self.cursor_y = dx, dy
        self.status(f"tap ({dx},{dy})")
        self.redraw()
        threading.Thread(
            target=lambda: adb("shell", "input", "tap", str(dx), str(dy)),
            daemon=True
        ).start()

    def do_swipe(self, direction):
        cx, cy = self.cursor_x, self.cursor_y
        d = self.SWIPE_DIST
        coords = {
            'Up':    (cx, cy, cx, cy - d),
            'Down':  (cx, cy, cx, cy + d),
            'Left':  (cx, cy, cx - d, cy),
            'Right': (cx, cy, cx + d, cy),
        }
        if direction not in coords:
            return
        x1, y1, x2, y2 = coords[direction]
        self.status(f"swipe {direction.lower()}")
        threading.Thread(
            target=lambda: adb("shell", "input", "swipe",
                               str(x1), str(y1), str(x2), str(y2), str(self.SWIPE_MS)),
            daemon=True
        ).start()

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self, msg):
        self.status_var.set(msg)

    def _idle_status(self):
        if self.hint_mode:
            self.status(f"hint: {''.join(self.hint_buf) or '_'}  (Esc=cancel)")
        else:
            self.status("f=hints  arrows=move  space=tap  s+arrow=swipe  r=refresh  Esc=quit")

    # ── Key handler ──────────────────────────────────────────────────────────

    def on_key(self, event):
        sym  = event.keysym
        char = event.char.upper() if event.char else ""

        shift  = bool(event.state & 0x1)
        option = bool(event.state & 0x8)

        # ── swipe mode (after pressing s) ────────────────────────────────
        if self.swipe_mode:
            self.swipe_mode = False
            if sym in ('Up', 'Down', 'Left', 'Right'):
                self.do_swipe(sym)
            else:
                self.status("swipe cancelled")
            return

        # ── hint mode ────────────────────────────────────────────────────
        if self.hint_mode:
            if sym == 'Escape':
                self.hint_mode = False
                self.hint_buf.clear()
                self.redraw()
                self._idle_status()
                return
            if sym == 'BackSpace' and self.hint_buf:
                self.hint_buf.pop()
                self.redraw()
                self._idle_status()
                return
            if char.isalpha() and not self.hint_buf:
                self.hint_buf.append(char)
                self.redraw()
                self._idle_status()
                return
            if char.isdigit() and len(self.hint_buf) == 1:
                self.hint_buf.append(char)
                label = ''.join(self.hint_buf)
                self.hint_mode = False
                self.hint_buf.clear()
                try:
                    dx, dy = self.cell_to_dev(label)
                    self.do_tap(dx, dy)
                except ValueError:
                    self.status(f"'{label}' out of range")
                    self.redraw()
                return
            return

        # ── normal mode ──────────────────────────────────────────────────
        if sym == 'Escape':
            self.root.destroy()
            return

        if char in ('F',):
            self.hint_mode = True
            self.hint_buf.clear()
            self.redraw()
            self._idle_status()
            return

        if char in ('R',):
            return  # capture loop is continuous

        if char in ('S',):
            self.swipe_mode = True
            self.status("swipe: press arrow key")
            return

        if sym == 'space':
            self.do_tap(self.cursor_x, self.cursor_y)
            return

        # cursor movement
        step = self.STEP_FINE if option else (self.STEP_FAST if shift else self.STEP_NORMAL)
        if sym == 'Up':
            self.cursor_y = max(0, self.cursor_y - step)
        elif sym == 'Down':
            self.cursor_y = min(self.dev_h, self.cursor_y + step)
        elif sym == 'Left':
            self.cursor_x = max(0, self.cursor_x - step)
        elif sym == 'Right':
            self.cursor_x = min(self.dev_w, self.cursor_x + step)
        else:
            return

        self.redraw()
        self.status(f"cursor ({self.cursor_x},{self.cursor_y})")

    # ── Run ──────────────────────────────────────────────────────────────────

    def run(self):
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
