"""
timeline_viewer.py  —  per-frame recording viewer.

Usage:
    python timeline_viewer.py [recording_dir]

Keyboard: left/right arrows (or buttons) to step through actions.
Displays the video frame at each action's timestamp alongside its events.
Falls back to static screenshots for recordings without screen.mp4.
"""
import base64
import json
import os
import sys
import tkinter as tk
from tkinter import filedialog

try:
    import av
    import numpy as np
    _AV_OK = True
except ImportError:
    _AV_OK = False


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
            d = json.load(f)
        name = d.get("name", name)

    video_path = None
    mp4 = os.path.join(dir_path, "screen.mp4")
    if os.path.exists(mp4):
        video_path = mp4

    return events, dir_path, name, video_path


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


# ── video frame extraction ────────────────────────────────────────────────────

def _frame_at_ms(container, t_ms: int):
    """Seek to t_ms and return first decoded frame as (numpy array, w, h)."""
    stream = container.streams.video[0]
    try:
        container.seek(t_ms, stream=stream, backward=True, any_frame=False)
    except Exception:
        container.seek(0)
    for frame in container.decode(stream):
        arr = frame.to_ndarray(format="rgb24")
        return arr, frame.width, frame.height
    return None, 0, 0


def _arr_to_photoimage(arr, target_h: int = 560):
    """Convert numpy RGB24 array to tk.PhotoImage via base64-encoded PPM."""
    H, W = arr.shape[:2]
    if H > target_h:
        scale = max(1, H // target_h)
        arr = arr[::scale, ::scale]
        H, W = arr.shape[:2]
    ppm = f"P6\n{W} {H}\n255\n".encode() + arr.tobytes()
    return tk.PhotoImage(data=base64.b64encode(ppm))


# ── event formatting ──────────────────────────────────────────────────────────

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
    t   = evt.get("t", 0)
    typ = evt.get("type", "?")
    if typ == "action":
        return (f"  [{t:>6}ms]  ACTION  {evt.get('action_type','?')}  {evt.get('hint','')}",
                "action")
    elif typ == "log":
        lvl = evt.get("level", "?")
        return (f"  [{t:>6}ms]  {lvl}/{evt.get('tag','?')}: {evt.get('msg','')}",
                f"log_{lvl}" if f"log_{lvl}" in _COLORS else "log_?")
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
        self.root      = root
        self.steps     = []
        self.idx       = 0
        self._img      = None
        self._video    = None   # av.Container, kept open for seeking

        root.title("keytap  |  timeline viewer")
        root.configure(bg=BG)
        root.minsize(900, 640)

        self._build_ui()
        self._load(dir_path)
        root.bind("<Left>",  lambda _: self._nav(-1))
        root.bind("<Right>", lambda _: self._nav(+1))
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        if self._video:
            try:
                self._video.close()
            except Exception:
                pass
        self.root.destroy()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
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

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True)

        ss_frame = tk.Frame(main, bg=BG, width=300)
        ss_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        ss_frame.pack_propagate(False)

        self.ss_lbl = tk.Label(ss_frame, bg=SIDE, text="no frame",
                               fg=DIM, font=FONT)
        self.ss_lbl.pack(fill=tk.BOTH, expand=True)

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

    # ── load ──────────────────────────────────────────────────────────────────

    def _load(self, dir_path: str):
        try:
            events, self.dir_path, name, video_path = load_recording(dir_path)
        except Exception as e:
            self.lbl_title.configure(text=str(e))
            return

        if video_path:
            if _AV_OK:
                try:
                    self._video = av.open(video_path)
                    # configure stream for faster seeking
                    self._video.streams.video[0].codec_context.skip_frame = "NONREF"
                except Exception as e:
                    self.lbl_title.configure(text=f"video open failed: {e}")
                    self._video = None
            else:
                self.ss_lbl.configure(text="install PyAV to enable video\n(pip install av)")

        self.steps = build_steps(events)
        self.lbl_title.configure(text=name)
        self.idx = 0
        self._render()

    # ── nav / render ──────────────────────────────────────────────────────────

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

        # frame
        if self._video:
            self._show_video_frame(act.get("t", 0))
        elif act.get("screenshot"):
            self._show_screenshot(os.path.join(self.dir_path, act["screenshot"]))
        else:
            self.ss_lbl.configure(image="", text="no frame")
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

    def _show_video_frame(self, t_ms: int):
        try:
            arr, w, h = _frame_at_ms(self._video, t_ms)
            if arr is not None:
                img = _arr_to_photoimage(arr)
                self._img = img
                self.ss_lbl.configure(image=img, text="")
                return
        except Exception as e:
            pass
        self.ss_lbl.configure(image="", text=f"seek failed @ {t_ms}ms")
        self._img = None

    def _show_screenshot(self, path: str):
        if not os.path.exists(path):
            self.ss_lbl.configure(image="", text="screenshot missing")
            self._img = None
            return
        try:
            img = tk.PhotoImage(file=path)
            w, h = img.width(), img.height()
            if h > 560:
                factor = max(1, h // 560)
                img = img.subsample(factor, factor)
            self._img = img
            self.ss_lbl.configure(image=img, text="")
        except Exception as e:
            self.ss_lbl.configure(image="", text=f"error: {e}")
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
