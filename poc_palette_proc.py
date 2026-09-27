"""
POC: Palette subprocess - receives JSON on stdin, shows tkinter window, writes result to stdout.
Run by parent via: python3 poc_palette_proc.py < data.json
"""
import json
import sys
import tkinter as tk
from tkinter import ttk


def main():
    data = json.loads(sys.stdin.readline())
    packages = data.get("packages", [])

    root = tk.Tk()
    root.title("keytap")
    root.geometry("520x440")
    root.configure(bg="#1c1c1e")
    root.attributes("-topmost", True)

    result = [None]

    def emit(r):
        result[0] = r
        root.destroy()

    # Search bar
    var = tk.StringVar()
    entry = tk.Entry(root, textvariable=var, bg="#2c2c2e", fg="white",
                     insertbackground="white", font=("Menlo", 14),
                     relief="flat", bd=8)
    entry.pack(fill="x", padx=12, pady=(12, 0))
    entry.focus_set()

    # List
    lb = tk.Listbox(root, bg="#1c1c1e", fg="#f0f0f0",
                    selectbackground="#3a3a3c", selectforeground="#00c864",
                    font=("Menlo", 12), relief="flat", bd=0,
                    activestyle="none")
    lb.pack(fill="both", expand=True, padx=12, pady=8)

    items = list(packages)

    def refresh(*_):
        q = var.get().lower()
        lb.delete(0, "end")
        for p in items:
            if q in p.lower():
                lb.insert("end", p)

    var.trace_add("write", refresh)
    refresh()

    def on_enter(_=None):
        sel = lb.curselection()
        if sel:
            emit({"type": "launch", "pkg": lb.get(sel[0])})
        else:
            emit(None)

    root.bind("<Return>", on_enter)
    root.bind("<Escape>", lambda _: emit(None))
    root.protocol("WM_DELETE_WINDOW", lambda: emit(None))

    root.mainloop()
    print(json.dumps(result[0]))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
