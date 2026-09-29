"""
timeline_viewer.py  —  per-frame recording viewer.

Usage:
    python timeline_viewer.py [recording_dir]

Keyboard: left/right arrows to step through actions.
"""
import json
import os
import sys
import tkinter as tk
from tkinter import filedialog


# ── data loading ──────────────────────────────────────────────────────────────

def load_recording(dir_path: str):
    tl_path = os.path.join(dir_path, "timeline.json")
    if not os.path.exists(tl_path):
        raise FileNotFoundError(f"No timeline.json in {dir_path}")
    with open(tl_path) as f:
        events = json.load(f).get("events", [])

    name = os.path.basename(dir_path)
    actions_path = os.path.join(dir_path, "actions.json")
    if os.path.exists(actions_path):
        with open(actions_path) as f:
            name = json.load(f).get("name", name)

    return events, dir_path, name


def build_steps(events):
    """Group events into per-action steps."""
    action_evts = sorted([e for e in events if e.get("type") == "action"],
                         key=lambda e: e.get("t", 0))
    other_evts  = sorted([e for e in events if e.get("type") != "action"],
                         key=lambda e: e.get("t", 0))
    steps = []
    for i, act in enumerate(action_evts):
        t_start = act.get("t", 0)
        t_end   = action_evts[i + 1].get("t", float("inf")) if i + 1 < len(action_evts) else float("inf")
        related = [e for e in other_evts if t_start <= e.get("t", 0) < t_end]
        steps.append({"action": act, "events": related})
    return steps


# ── event formatting ──────────────────────────────────────────────────────────

# (color, bold)
_COLORS = {
    "action":      ("#4fc3f7", True),
    "log_E":       ("#ef5350", False),
    "log_W":       ("#ffa726", False),
    "log_I":       ("#aed581", False),
    "log_?":       ("#b0bec5", False),
    "network_req": ("#ce93d8", False),
    "network_res": ("#80cbc4", False),
}


def fmt(evt):
    """Return (text, color_key)."""
    t   = evt.get("t", 0)
    typ = evt.get("type", "?")
    if typ == "action":
        return (f"  [{t:>6}ms]  ACTION  {evt.get('action_type','?')}  {evt.get('hint','')}",
                "action")
    elif typ == "log":
        lvl = evt.get("level", "?")
        s = f"  [{t:>6}ms]  {lvl}/{evt.get('tag','?')}: {evt.get('msg','')}"
        return s, (f"log_{lvl}" if f"log_{lvl}" in _COLORS else "log_?")
    elif typ == "network_req":
        return (f"  [{t:>6}ms]  --> {evt.get('method','?')} {evt.get('url','?')}",
                "network_req")
    elif typ == "network_res":
        dur = evt.get("duration_ms")
        dur_s = f" ({dur}ms)" if dur is not None else ""
        return (f"  [{t:>6}ms]  <-- {evt.get('status','?')} {evt.get('method','?')} {evt.get('url','?')}{dur_s}",
                "network_res")
    return f"  [{t:>6}ms]  {typ}", "log_?"


# ── viewer ────────────────────────────────────────────────────────────────────

BG   = "#1e1e2e"
SIDE = "#181825"
FG   = "#cdd6f4"
DIM  = "#6c7086"
BTN  = "#313244"
FONT = ("Menlo", 11)


class TimelineViewer:

    def __init__(self, root: tk.Tk, dir_path: str):
        self.root  = root
        self.steps = []
        self.idx   = 0
        self._img  = None

        root.title("keytap  |  timeline viewer")
        root.configure(bg=BG)
        root.minsize(900, 640)

        self._build_ui()
        self._load(dir_path)
        root.bind("<Left>",  lambda _: self._nav(-1))
        root.bind("<Right>", lambda _: self._nav(+1))

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # top bar
        bar = tk.Frame(self.root, bg=SIDE, pady=6)
        bar.pack(fill=tk.X)

        self.lbl_title = tk.Label(bar, text="", bg=SIDE, fg=FG,
                                  font=("Menlo", 13, "bold"))
        self.lbl_title.pack(side=tk.LEFT, padx=12)

        for text, delta in [("◀  Prev", -1), ("Next  ▶", +1)]:
            tk.Button(bar, text=text, command=lambda d=delta: self._nav(d),
                      bg=BTN, fg=FG, relief=tk.FLAT,
                      padx=10, pady=4, font=FONT).pack(side=tk.RIGHT, padx=4)

        self.lbl_step = tk.Label(bar, text="", bg=SIDE, fg=DIM, font=FONT)
        self.lbl_step.pack(side=tk.RIGHT, padx=12)

        # main
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True)

        # screenshot panel (fixed width)
        ss_frame = tk.Frame(main, bg=BG, width=300)
        ss_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        ss_frame.pack_propagate(False)

        self.ss_lbl = tk.Label(ss_frame, bg=SIDE, text="no screenshot",
                               fg=DIM, font=FONT)
        self.ss_lbl.pack(fill=tk.BOTH, expand=True)

        # events panel
        ev = tk.Frame(main, bg=BG)
        ev.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10), pady=10)

        tk.Label(ev, text="Events", bg=BG, fg=DIM, font=FONT).pack(anchor=tk.W, pady=(0, 4))

        self.txt = tk.Text(ev, bg=SIDE, fg=FG, font=FONT,
                           relief=tk.FLAT, wrap=tk.WORD,
                           state=tk.DISABLED, cursor="arrow")
        sb = tk.Scrollbar(ev, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt.pack(fill=tk.BOTH, expand=True)

        for key, (color, bold) in _COLORS.items():
            self.txt.tag_configure(key, foreground=color,
                                   font=("Menlo", 11, "bold" if bold else "normal"))

    # ── load / nav / render ──────────────────────────────────────────────────

    def _load(self, dir_path: str):
        try:
            events, self.dir_path, name = load_recording(dir_path)
        except Exception as e:
            self.lbl_title.configure(text=str(e))
            return
        self.steps = build_steps(events)
        self.lbl_title.configure(text=name)
        self.idx = 0
        self._render()

    def _nav(self, delta: int):
        if not self.steps:
            return
        self.idx = max(0, min(len(self.steps) - 1, self.idx + delta))
        self._render()

    def _render(self):
        if not self.steps:
            return
        step = self.steps[self.idx]
        act  = step["action"]

        self.lbl_step.configure(text=f"Step {self.idx + 1} / {len(self.steps)}")

        # screenshot
        ss_path = act.get("screenshot")
        if ss_path:
            self._show_screenshot(os.path.join(self.dir_path, ss_path))
        else:
            self.ss_lbl.configure(image="", text="no screenshot")
            self._img = None

        # events
        self.txt.configure(state=tk.NORMAL)
        self.txt.delete("1.0", tk.END)

        text, ckey = fmt(act)
        self.txt.insert(tk.END, text + "\n", ckey)
        self.txt.insert(tk.END, "\n")

        if step["events"]:
            self.txt.insert(tk.END, "  -- related events --\n\n", "log_?")
            for evt in step["events"]:
                text, ckey = fmt(evt)
                self.txt.insert(tk.END, text + "\n", ckey)
        else:
            self.txt.insert(tk.END, "  (no log / network events for this step)\n", "log_?")

        self.txt.configure(state=tk.DISABLED)

    def _show_screenshot(self, path: str):
        if not os.path.exists(path):
            self.ss_lbl.configure(image="", text="screenshot missing")
            self._img = None
            return
        try:
            img = tk.PhotoImage(file=path)
            w, h = img.width(), img.height()
            target_h = 580
            if h > target_h:
                factor = max(1, h // target_h)
                img = img.subsample(factor, factor)
            self._img = img
            self.ss_lbl.configure(image=img, text="")
        except Exception as e:
            self.ss_lbl.configure(image="", text=f"error loading: {e}")
            self._img = None


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    if len(sys.argv) > 1:
        dir_path = sys.argv[1]
    else:
        dir_path = filedialog.askdirectory(title="Select recording directory")
        if not dir_path:
            root.destroy()
            return
    TimelineViewer(root, dir_path)
    root.mainloop()


if __name__ == "__main__":
    main()
