import subprocess
import threading
import queue
import time

from PIL import Image

from config import ARGS, HAS_FFMPEG
from adb import adb, take_screencap
from settings import section as _s


class CaptureManager:
    """h264 pipe capture: screenrecord -> ffmpeg -> PIL frames.

    Falls back to per-frame screencap if ffmpeg is missing.
    frame_q always contains PIL RGB Images sized (win_w, win_h).
    """

    def __init__(self, win_w, win_h, on_status):
        self.win_w       = win_w
        self.win_h       = win_h
        self._on_status  = on_status   # callable(str), must be thread-safe
        self.frame_q     = queue.Queue(maxsize=1)
        self._running    = True
        self._adb_proc   = None
        cap = _s("capture")
        self.bitrate     = str(cap.get("bitrate", "8000000"))
        self.low_latency = bool(cap.get("low_latency", False))

    def start(self):
        if HAS_FFMPEG:
            threading.Thread(target=self._h264_loop, daemon=True).start()
        else:
            self._on_status("ffmpeg missing - using screencap (slow)")
            threading.Thread(target=self._screencap_loop, daemon=True).start()

    def stop(self):
        self._running = False

    def restart_with_settings(self, bitrate=None, low_latency=None):
        """Apply new capture settings and restart the stream immediately."""
        if bitrate is not None:
            self.bitrate = str(bitrate)
        if low_latency is not None:
            self.low_latency = bool(low_latency)
        if self._adb_proc:
            try:
                self._adb_proc.terminate()
            except Exception:
                pass

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _enqueue(self, frame):
        try:
            self.frame_q.get_nowait()
        except queue.Empty:
            pass
        self.frame_q.put(frame)

    def _adb_args(self):
        """Return base adb arg list with -s serial if set."""
        from config import ADB_PATH
        args = [ADB_PATH]
        if ARGS.serial:
            args += ["-s", ARGS.serial]
        return args

    # ── h264 pipeline ────────────────────────────────────────────────────────

    def _h264_loop(self):
        """Outer restart loop - handles screenrecord 3-min limit."""
        consecutive_failures = 0
        while self._running:
            ok = self._h264_session()
            if ok:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    self._on_status("stream failing repeatedly - check device")
                    time.sleep(5)
                    consecutive_failures = 0
                else:
                    time.sleep(1)

    def _h264_session(self):
        """One screenrecord session. Returns True if it ran for a meaningful time."""
        frame_bytes = self.win_w * self.win_h * 3  # RGB24

        adb_cmd = self._adb_args() + [
            "shell", "screenrecord",
            "--output-format=h264",
            f"--bit-rate={self.bitrate}",
            "-",
        ]
        ffmpeg_cmd = ["ffmpeg", "-loglevel", "quiet", "-hwaccel", "videotoolbox"]
        if self.low_latency:
            ffmpeg_cmd += ["-flags", "low_delay", "-fflags", "nobuffer",
                           "-probesize", "32", "-analyzeduration", "0"]
        ffmpeg_cmd += [
            "-i", "pipe:0",
            "-vf", f"scale={self.win_w}:{self.win_h}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]

        try:
            adb_proc = subprocess.Popen(
                adb_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=adb_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._adb_proc = adb_proc
        except Exception as e:
            self._on_status(f"capture start error: {e}")
            return False

        t_start     = time.time()
        first_frame = True
        frame_count = 0

        try:
            while self._running:
                data = ffmpeg_proc.stdout.read(frame_bytes)
                if len(data) != frame_bytes:
                    # Stream ended (device disconnected, 3-min limit, etc.)
                    elapsed = time.time() - t_start
                    if frame_count > 0:
                        self._on_status("stream ended - restarting...")
                    return frame_count > 10  # considered successful if we got frames

                frame = Image.frombuffer("RGB", (self.win_w, self.win_h), data)
                self._enqueue(frame)
                frame_count += 1

                if first_frame:
                    elapsed = time.time() - t_start
                    self._on_status(f"streaming  |  first frame {elapsed:.2f}s")
                    first_frame = False

        finally:
            try:
                adb_proc.terminate()
            except Exception:
                pass
            try:
                ffmpeg_proc.terminate()
            except Exception:
                pass

        return frame_count > 10

    # ── Screencap fallback ───────────────────────────────────────────────────

    def _screencap_loop(self):
        import io
        while self._running:
            try:
                png = take_screencap()
                if png and len(png) > 512:
                    img = Image.open(io.BytesIO(png))
                    frame = img.resize((self.win_w, self.win_h), Image.BILINEAR)
                    self._enqueue(frame)
                else:
                    time.sleep(0.5)
            except Exception as e:
                self._on_status(f"capture error: {e}")
                time.sleep(0.5)
