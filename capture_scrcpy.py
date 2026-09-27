"""
ScrcpyCaptureManager: scrcpy-server capture backend.

Replaces screenrecord with scrcpy-server which hooks SurfaceFlinger
directly, achieving ~30fps vs screenrecord's 2-10fps.

Protocol (raw_stream=true):
  scrcpy-server → abstract socket → adb forward → TCP → Python socket
  Python socket → feed thread → ffmpeg stdin → RGB24 frames → frame_q

Same public interface as CaptureManager.

Usage:
  python app.py --backend scrcpy
"""

import socket
import subprocess
import threading
import queue
import time
import urllib.request
from pathlib import Path

from config import ARGS, ADB_PATH, HAS_FFMPEG
from settings import section as _s


SCRCPY_VERSION     = "4.1"
SCRCPY_PORT        = 27183
SCRCPY_DEVICE_PATH = "/data/local/tmp/scrcpy-server.jar"

_CACHE_DIR    = Path.home() / ".keytap" / "scrcpy"
_LOCAL_JAR    = _CACHE_DIR / f"scrcpy-server-v{SCRCPY_VERSION}.jar"
_DOWNLOAD_URL = (
    f"https://github.com/Genymobile/scrcpy/releases/download/"
    f"v{SCRCPY_VERSION}/scrcpy-server-v{SCRCPY_VERSION}"
)


class ScrcpyCaptureManager:
    """scrcpy-server capture backend. Drop-in replacement for CaptureManager."""

    def __init__(self, win_w, win_h, dev_w, dev_h, on_status):
        self.win_w        = win_w
        self.win_h        = win_h
        self.dev_w        = dev_w
        self.dev_h        = dev_h
        self._on_status   = on_status
        self.frame_q      = queue.Queue(maxsize=1)
        self._running     = True
        self._server_proc = None
        self._sock        = None

        cap = _s("capture")
        self.bitrate      = str(cap.get("bitrate", "8000000"))
        self.low_latency  = bool(cap.get("low_latency", False))
        self.fps_cap      = int(cap.get("fps_cap", 0))
        self.encode_scale = float(cap.get("encode_scale", 1.0))

        # Live stats (GIL-safe, same shape as CaptureManager)
        self.stats       = {"producer_fps": 0.0, "dropped_ps": 0, "stall_ms": 0.0}
        self._stat_prod  = 0
        self._stat_drop  = 0
        self._stat_t     = 0.0

    def start(self):
        if not HAS_FFMPEG:
            self._on_status("ffmpeg missing - scrcpy backend requires ffmpeg")
            return
        threading.Thread(target=self._scrcpy_loop, daemon=True).start()

    def stop(self):
        self._running = False
        self._kill_server()

    def restart_with_settings(self, bitrate=None, low_latency=None, fps_cap=None, encode_scale=None):
        if bitrate is not None:      self.bitrate = str(bitrate)
        if low_latency is not None:  self.low_latency = bool(low_latency)
        if fps_cap is not None:      self.fps_cap = int(fps_cap)
        if encode_scale is not None: self.encode_scale = float(encode_scale)
        self._kill_server()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _adb(self):
        args = [ADB_PATH]
        if ARGS.serial:
            args += ["-s", ARGS.serial]
        return args

    def _enqueue(self, frame):
        try:
            self.frame_q.get_nowait()
            self._stat_drop += 1
        except queue.Empty:
            pass
        self.frame_q.put(frame)
        self._stat_prod += 1
        now = time.time()
        if self._stat_t == 0.0:
            self._stat_t = now
        elif now - self._stat_t >= 1.0:
            elapsed = now - self._stat_t
            self.stats["producer_fps"] = round(self._stat_prod / elapsed, 1)
            self.stats["dropped_ps"]   = self._stat_drop
            self._stat_prod = 0
            self._stat_drop = 0
            self._stat_t    = now

    def _kill_server(self):
        if self._server_proc:
            try:
                self._server_proc.terminate()
            except Exception:
                pass
            self._server_proc = None
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _ensure_jar(self):
        if _LOCAL_JAR.exists():
            return True
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._on_status(f"downloading scrcpy-server v{SCRCPY_VERSION}...")
        try:
            urllib.request.urlretrieve(_DOWNLOAD_URL, _LOCAL_JAR)
            self._on_status("scrcpy-server downloaded")
            return True
        except Exception as e:
            self._on_status(f"scrcpy download failed: {e}")
            return False

    def _push_jar(self):
        # Skip if device already has matching file size
        check = subprocess.run(
            self._adb() + ["shell", f"stat -c %s {SCRCPY_DEVICE_PATH} 2>/dev/null || echo 0"],
            capture_output=True, text=True
        )
        device_size = int((check.stdout.strip() or "0").splitlines()[-1] or "0")
        local_size  = _LOCAL_JAR.stat().st_size
        if device_size == local_size:
            return True
        self._on_status("pushing scrcpy-server to device...")
        r = subprocess.run(
            self._adb() + ["push", str(_LOCAL_JAR), SCRCPY_DEVICE_PATH],
            capture_output=True
        )
        if r.returncode != 0:
            self._on_status("push failed")
            return False
        return True

    def _forward_port(self):
        subprocess.run(
            self._adb() + ["forward", f"tcp:{SCRCPY_PORT}", "localabstract:scrcpy"],
            capture_output=True
        )

    def _start_server(self):
        max_size = int(self.dev_h * self.encode_scale) if self.encode_scale < 1.0 else 0

        server_args = [
            f"CLASSPATH={SCRCPY_DEVICE_PATH}",
            "app_process", "/",
            "com.genymobile.scrcpy.Server", SCRCPY_VERSION,
            "tunnel_forward=true",
            "audio=false",
            "control=false",
            "cleanup=false",
            "raw_stream=true",
            f"video_bit_rate={self.bitrate}",
        ]
        if max_size > 0:
            server_args.append(f"max_size={max_size}")
        if self.fps_cap > 0:
            server_args.append(f"max_fps={self.fps_cap}")

        self._server_proc = subprocess.Popen(
            self._adb() + ["shell"] + server_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # ── Capture loop ──────────────────────────────────────────────────────────

    def _scrcpy_loop(self):
        if not self._ensure_jar():
            return
        if not self._push_jar():
            return

        consecutive_failures = 0
        while self._running:
            ok = self._scrcpy_session()
            if ok:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    self._on_status("scrcpy stream failing - check device")
                    time.sleep(5)
                    consecutive_failures = 0
                else:
                    time.sleep(1)

    def _scrcpy_session(self):
        frame_bytes = self.win_w * self.win_h * 3  # RGB24

        self._forward_port()
        self._start_server()

        # Wait for server to start listening
        time.sleep(0.4)

        # Connect socket with retries
        sock = None
        for _ in range(10):
            try:
                sock = socket.create_connection(("127.0.0.1", SCRCPY_PORT), timeout=1.0)
                sock.settimeout(None)
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.2)

        if sock is None:
            self._on_status("scrcpy: could not connect to server")
            self._kill_server()
            return False
        self._sock = sock

        # ffmpeg: reads raw H264 from stdin, outputs RGB24 to stdout
        vf = f"scale={self.win_w}:{self.win_h}"
        if self.fps_cap > 0:
            vf += f",fps={self.fps_cap}"
        ffmpeg_cmd = ["ffmpeg", "-loglevel", "quiet", "-hwaccel", "videotoolbox"]
        if self.low_latency:
            ffmpeg_cmd += ["-flags", "low_delay", "-fflags", "nobuffer+discardcorrupt",
                           "-probesize", "2048", "-analyzeduration", "100000",
                           "-avioflags", "direct"]
        ffmpeg_cmd += ["-i", "pipe:0", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]

        try:
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self._on_status(f"ffmpeg start error: {e}")
            self._kill_server()
            sock.close()
            return False

        # Feed thread: copy socket bytes → ffmpeg stdin
        def _feed():
            try:
                while self._running:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    ffmpeg_proc.stdin.write(chunk)
            except Exception:
                pass
            finally:
                try:
                    ffmpeg_proc.stdin.close()
                except Exception:
                    pass

        threading.Thread(target=_feed, daemon=True).start()

        t_start     = time.time()
        first_frame = True
        frame_count = 0

        try:
            while self._running:
                t0   = time.time()
                data = ffmpeg_proc.stdout.read(frame_bytes)
                stall_ms = (time.time() - t0) * 1000
                self.stats["stall_ms"] = round(
                    0.8 * self.stats["stall_ms"] + 0.2 * stall_ms, 1
                )
                if len(data) != frame_bytes:
                    if frame_count > 0:
                        self._on_status("scrcpy stream ended - restarting...")
                    return frame_count > 10

                self._enqueue(data)
                frame_count += 1

                if first_frame:
                    elapsed = time.time() - t_start
                    self._on_status(f"scrcpy  |  first frame {elapsed:.2f}s")
                    first_frame = False

        finally:
            try:
                ffmpeg_proc.terminate()
            except Exception:
                pass
            self._kill_server()

        return frame_count > 10
