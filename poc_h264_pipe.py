#!/usr/bin/env python3
"""
POC: screenrecord H.264 -> ffmpeg (hwaccel + scale to display) -> Tkinter
Tweaks active:
  - VideoToolbox hardware decode
  - ffmpeg scales directly to display size (no PIL resize)
  - screenrecord 8Mbps bitrate
  - 8ms display poll (~120fps)
Press Esc to quit.
"""

import queue
import re
import subprocess
import threading
import time
import tkinter as tk
from PIL import Image, ImageTk

ADB    = "/Users/sidharthsharma/Library/Android/sdk/platform-tools/adb"
SERIAL = "XSO7ZXXCI7IZ4HY5"
DISPLAY_HEIGHT = 700
BITRATE        = "8000000"  # screenrecord bitrate


def get_device_size():
    out = subprocess.run(
        [ADB, "-s", SERIAL, "shell", "wm", "size"],
        capture_output=True,
    ).stdout.decode()
    m = re.search(r"(\d+)x(\d+)", out)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        return (w, h) if w < h else (h, w)
    return 1080, 2400


def main():
    dev_w, dev_h = get_device_size()
    scale = DISPLAY_HEIGHT / dev_h
    win_w = int(dev_w * scale)
    win_h = DISPLAY_HEIGHT

    # ffmpeg outputs exactly at display size — no PIL resize needed
    frame_bytes = win_w * win_h * 3

    print(f"device {dev_w}x{dev_h} | display {win_w}x{win_h} | frame {frame_bytes:,}B")

    root = tk.Tk()
    root.title("keytap POC — h264 hwaccel")
    root.configure(bg="black")

    canvas = tk.Canvas(root, width=win_w, height=win_h, bg="black", highlightthickness=0)
    canvas.pack()
    img_id = canvas.create_image(0, 0, anchor="nw")

    fps_var = tk.StringVar(value="fps: starting...")
    tk.Label(root, textvariable=fps_var, bg="black", fg="#00ff88",
             font=("Menlo", 12)).pack(pady=4)

    photo_ref = [None]
    frame_q   = queue.Queue(maxsize=1)
    procs     = []

    # ── Pipeline ──────────────────────────────────────────────────────────────
    adb_proc = subprocess.Popen(
        [ADB, "-s", SERIAL, "shell",
         "screenrecord", "--output-format=h264", f"--bit-rate={BITRATE}", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    ffmpeg_proc = subprocess.Popen(
        [
            "ffmpeg", "-loglevel", "quiet",
            "-hwaccel", "videotoolbox",       # GPU decode on macOS
            "-i", "pipe:0",
            "-vf", f"scale={win_w}:{win_h}",  # scale once in ffmpeg, skip PIL resize
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ],
        stdin=adb_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    procs.extend([adb_proc, ffmpeg_proc])

    # ── Reader thread ─────────────────────────────────────────────────────────
    def read_frames():
        frame_cnt = 0
        last_t    = time.time()
        while True:
            try:
                data = ffmpeg_proc.stdout.read(frame_bytes)
            except Exception as e:
                print(f"read error: {e}")
                break
            if len(data) < frame_bytes:
                print(f"stream ended (got {len(data)}, expected {frame_bytes})")
                break

            frame_cnt += 1
            now     = time.time()
            elapsed = now - last_t
            if elapsed >= 1.0:
                fps       = frame_cnt / elapsed
                frame_cnt = 0
                last_t    = now
                root.after(0, lambda f=fps: fps_var.set(f"fps: {f:.1f}"))

            # No PIL resize — ffmpeg already output at display size
            img = Image.frombuffer("RGB", (win_w, win_h), data)
            try:
                frame_q.put_nowait(img)
            except queue.Full:
                pass  # drop frame

    # ── Display loop ─────────────────────────────────────────────────────────
    def display_loop():
        try:
            img   = frame_q.get_nowait()
            photo = ImageTk.PhotoImage(img)
            photo_ref[0] = photo
            canvas.itemconfig(img_id, image=photo)
        except queue.Empty:
            pass
        root.after(8, display_loop)  # 120fps poll

    def on_close():
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.bind("<Escape>", lambda _: on_close())

    threading.Thread(target=read_frames, daemon=True).start()
    display_loop()
    root.mainloop()


if __name__ == "__main__":
    main()
