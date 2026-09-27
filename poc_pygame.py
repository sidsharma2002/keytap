#!/usr/bin/env python3
"""
POC: screenrecord H.264 -> ffmpeg -> pygame
No PIL. No Tkinter. Raw bytes -> pygame surface -> GPU blit.
Press Esc or Q to quit.
"""

import queue
import re
import subprocess
import threading
import time

import pygame

ADB    = "/Users/sidharthsharma/Library/Android/sdk/platform-tools/adb"
SERIAL = "XSO7ZXXCI7IZ4HY5"
DISPLAY_HEIGHT = 700
BITRATE        = "8000000"


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
    frame_bytes = win_w * win_h * 3  # RGB24, exact display size

    print(f"device {dev_w}x{dev_h} | display {win_w}x{win_h} | frame {frame_bytes:,}B")

    pygame.init()
    screen = pygame.display.set_mode((win_w, win_h))
    pygame.display.set_caption("keytap POC — pygame")
    clock = pygame.time.Clock()

    frame_q = queue.Queue(maxsize=1)
    procs   = []
    running = [True]

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
            "-hwaccel", "videotoolbox",
            "-i", "pipe:0",
            "-vf", f"scale={win_w}:{win_h}",
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
        fps_label = [0.0]

        while running[0]:
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
                fps_label[0] = frame_cnt / elapsed
                print(f"fps: {fps_label[0]:.1f}", flush=True)
                frame_cnt = 0
                last_t    = now

            # pygame.image.frombuffer: zero-copy surface from raw bytes
            surf = pygame.image.frombuffer(data, (win_w, win_h), "RGB")
            try:
                frame_q.put_nowait((surf, fps_label[0]))
            except queue.Full:
                pass  # drop frame

    threading.Thread(target=read_frames, daemon=True).start()

    # ── Main loop (pygame, runs on main thread) ───────────────────────────────
    current_fps = 0.0
    while running[0]:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running[0] = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running[0] = False

        try:
            surf, current_fps = frame_q.get_nowait()
            screen.blit(surf, (0, 0))
            pygame.display.flip()
        except queue.Empty:
            pass

        clock.tick(120)  # cap at 120fps

    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    pygame.quit()


if __name__ == "__main__":
    main()
