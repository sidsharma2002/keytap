"""
POC: pygame main loop spawns palette as subprocess. Press SPACE to open.
Confirms subprocess approach works before full implementation.
"""
import json
import queue
import subprocess
import sys
import threading

import pygame

result_q = queue.Queue()
FAKE_PACKAGES = [
    "com.gojek.app",
    "com.google.android.chrome",
    "com.whatsapp",
    "com.instagram.android",
    "com.spotify.music",
]


def open_palette():
    data = json.dumps({"packages": FAKE_PACKAGES}) + "\n"
    try:
        proc = subprocess.Popen(
            [sys.executable, "poc_palette_proc.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        stdout, _ = proc.communicate(input=data.encode(), timeout=60)
        result = json.loads(stdout.decode().strip()) if stdout.strip() else None
        result_q.put(result)
    except Exception as e:
        print(f"[POC] subprocess error: {e}")
        result_q.put(None)


pygame.init()
screen = pygame.display.set_mode((360, 640))
pygame.display.set_caption("keytap POC")
clock = pygame.time.Clock()

print("Press SPACE to open palette, Esc to quit")
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                running = False
            elif event.key == pygame.K_SPACE:
                print("[POC] spawning palette subprocess...")
                threading.Thread(target=open_palette, daemon=True).start()

    try:
        r = result_q.get_nowait()
        print(f"[POC] result: {r}")
    except queue.Empty:
        pass

    screen.fill((20, 20, 20))
    pygame.display.flip()
    clock.tick(60)

pygame.quit()
