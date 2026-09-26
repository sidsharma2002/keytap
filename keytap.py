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


class KeyTap:
    def __init__(self, dev_w, dev_h):
        self.dev_w = dev_w
        self.dev_h = dev_h
        self.scale  = ARGS.height / dev_h
        self.win_w  = int(dev_w * self.scale)
        self.win_h  = ARGS.height

        self.input_buf  = []
        self.cursor_row = 0
        self.cursor_col = 0
        self.photo      = None
        self._raw_frame = None
        self._frame_q   = queue.Queue(maxsize=1)
        self._grid_font = self._load_grid_font()

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
            is_cursor = CURSOR_MODE and r == self.cursor_row and c == self.cursor_col

            for c in range(ARGS.cols):
                x0, y0 = int(c * cw), int(r * ch)
                x1, y1 = int((c + 1) * cw), int((r + 1) * ch)
                is_cursor = CURSOR_MODE and r == self.cursor_row and c == self.cursor_col
                label = f"{row_letter}{string.ascii_uppercase[c]}" if row_letter else None

                if not typed:
                    draw.rectangle([x0, y0, x1 - 1, y1 - 1],
                                   outline=(255, 255, 255, 38))
                    if label:
                        self._draw_pill(draw, x0, y0, label, active=False)
                elif row_match:
                    draw.rectangle([x0, y0, x1, y1], fill=(255, 215, 0, 28))
                    draw.rectangle([x0, y0, x1 - 1, y1 - 1],
                                   outline=(255, 215, 0, 200))
                    if label:
                        self._draw_pill(draw, x0, y0, label, active=True)
                else:
                    draw.rectangle([x0, y0, x1 - 1, y1 - 1],
                                   outline=(255, 255, 255, 15))
                    if label:
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

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self, msg):
        self.status_var.set(msg)

    def _idle_status(self):
        if CURSOR_MODE:
            r, c = self.cursor_row, self.cursor_col
            row_label = string.ascii_uppercase[r] if r < 26 else str(r + 1)
            col_label = string.ascii_uppercase[c]
            self.status(f"cursor: {row_label}{col_label} (row {r+1}, col {c+1})  |  arrows=move  space=tap  Esc=quit")
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

        if CURSOR_MODE:
            if sym == 'Up':
                self.cursor_row = max(0, self.cursor_row - 1)
            elif sym == 'Down':
                self.cursor_row = min(ARGS.rows - 1, self.cursor_row + 1)
            elif sym == 'Left':
                self.cursor_col = max(0, self.cursor_col - 1)
            elif sym == 'Right':
                self.cursor_col = min(ARGS.cols - 1, self.cursor_col + 1)
            elif sym == 'space':
                dx, dy = self.cell_to_dev_rc(self.cursor_row, self.cursor_col)
                self.do_tap(dx, dy)
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
