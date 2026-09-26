import subprocess
import threading
import queue
import time
import io
import os

from PIL import Image

from config import ARGS, FIFO_PATH, HAS_SCRCPY, HAS_FFMPEG
from adb import adb, take_screencap


class CaptureManager:
    """Manages the video capture pipeline. Produces PIL frames into frame_q."""

    def __init__(self, win_w, win_h, on_status):
        self.win_w        = win_w
        self.win_h        = win_h
        self._on_status   = on_status   # callable(msg: str) - must be thread-safe
        self.frame_q      = queue.Queue(maxsize=1)
        self._scrcpy_live = threading.Event()  # set when scrcpy stream produces first frame

    def start(self):
        if HAS_SCRCPY and HAS_FFMPEG:
            threading.Thread(target=self._screencap_until_live, daemon=True).start()
            threading.Thread(target=self._scrcpy_stream, daemon=True).start()
        else:
            missing = [x for x, ok in [("scrcpy", HAS_SCRCPY), ("ffmpeg", HAS_FFMPEG)] if not ok]
            self._on_status(f"missing {','.join(missing)} - using screencap")
            threading.Thread(target=self._screencap_loop, daemon=True).start()

    def _enqueue(self, frame):
        try:
            self.frame_q.get_nowait()
        except queue.Empty:
            pass
        self.frame_q.put(frame)

    def _png_to_frame(self, png):
        img = Image.open(io.BytesIO(png))
        return img.resize((self.win_w, self.win_h), Image.BILINEAR)

    def _screencap_until_live(self):
        """Poll screencap every 2s while scrcpy is connecting, stop once stream is live."""
        while not self._scrcpy_live.is_set():
            try:
                png = take_screencap()
                if png and len(png) > 512:
                    self._enqueue(self._png_to_frame(png))
            except Exception:
                pass
            self._scrcpy_live.wait(timeout=2.0)

    def _scrcpy_stream(self):
        try:
            os.unlink(FIFO_PATH)
        except FileNotFoundError:
            pass
        os.mkfifo(FIFO_PATH)

        scrcpy_cmd = ["scrcpy", "--record", FIFO_PATH, "--record-format=mkv",
                      "--no-video-playback", "--no-audio"]
        if ARGS.serial:
            scrcpy_cmd += [f"--serial={ARGS.serial}"]

        # NOTE: -fflags nobuffer breaks the matroska demuxer (0 frames produced).
        # Root cause: nobuffer prevents the MKV Tracks element from being buffered,
        # so the demuxer never finds a decodable video stream.
        # -analyzeduration 0 also breaks FIFO input: ffmpeg does a non-blocking open,
        # gets nothing before scrcpy connects (~8s), and immediately exits with 0 frames.
        ffmpeg_cmd = [
            "ffmpeg",
            "-flags", "low_delay",
            "-f", "matroska", "-i", FIFO_PATH,
            "-vf", f"scale={self.win_w}:{self.win_h}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]

        t_start     = time.time()
        scrcpy_proc = subprocess.Popen(scrcpy_cmd, stderr=subprocess.DEVNULL)
        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._on_status("connecting scrcpy...")

        frame_size  = self.win_w * self.win_h * 3
        first_frame = True
        while True:
            data = ffmpeg_proc.stdout.read(frame_size)
            if len(data) != frame_size:
                self._on_status("stream ended - restarting...")
                ffmpeg_proc.wait()
                scrcpy_proc.wait()
                time.sleep(1)
                threading.Thread(target=self._scrcpy_stream, daemon=True).start()
                return
            if first_frame:
                elapsed = time.time() - t_start
                print(f"[keytap] first scrcpy frame in {elapsed:.2f}s", flush=True)
                self._on_status(f"streaming  |  first frame in {elapsed:.2f}s")
                self._scrcpy_live.set()  # stop screencap polling
                first_frame = False
            self._enqueue(Image.frombytes("RGB", (self.win_w, self.win_h), data))

    def _screencap_loop(self):
        while True:
            try:
                png = take_screencap()
                if png and len(png) > 512:
                    self._enqueue(self._png_to_frame(png))
                else:
                    time.sleep(0.5)
            except Exception as e:
                self._on_status(f"capture error: {e}")
                time.sleep(0.5)
