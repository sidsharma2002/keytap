"""
POC: Can we open a tkinter window from a background thread while pygame runs on main thread?
Press SPACE to trigger a fake palette popup.
"""
import queue
import threading
import time

import pygame

result_q = queue.Queue()

def open_tk_palette():
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        # Open a Toplevel as the real palette window
        win = tk.Toplevel(root)
        win.title("keytap palette")
        win.geometry("500x400")
        win.configure(bg="#1c1c1e")
        win.attributes("-topmost", True)

        label = tk.Label(win, text="Command Palette POC\nPress Enter to select, Esc to close",
                        bg="#1c1c1e", fg="white", font=("Menlo", 14))
        label.pack(pady=30)

        btn = tk.Button(win, text="Select App", bg="#2c2c2e", fg="#00c864",
                       font=("Menlo", 12), relief="flat",
                       command=lambda: (result_q.put("selected: com.example.app"), win.destroy(), root.destroy()))
        btn.pack(pady=10)

        win.bind("<Escape>", lambda e: (result_q.put(None), win.destroy(), root.destroy()))
        win.protocol("WM_DELETE_WINDOW", lambda: (result_q.put(None), win.destroy(), root.destroy()))

        win.focus_force()
        root.mainloop()
    except Exception as e:
        print(f"[POC] tkinter failed: {e}")
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
                print("[POC] opening tk palette in thread...")
                threading.Thread(target=open_tk_palette, daemon=True).start()

    # Check for result
    try:
        r = result_q.get_nowait()
        print(f"[POC] result: {r}")
    except queue.Empty:
        pass

    screen.fill((20, 20, 20))
    pygame.display.flip()
    clock.tick(60)

pygame.quit()
