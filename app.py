import json
import os
import queue
import string
import subprocess
import sys
import threading
import time

import pygame

import actions
import fonts
from adb import adb, get_device_size, list_devices
from capture import CaptureManager
from config import ARGS, CURSOR_MODE, CURSOR_JUMP, ADB_PATH
from element_manager import ElementManager
from grid import GridRenderer
from settings import kb

STATUS_H = 24   # height of status bar below the mirror
_PROC    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "palette_proc.py")


class KeyTap:
    def __init__(self, dev_w, dev_h):
        self.dev_w  = dev_w
        self.dev_h  = dev_h
        self.scale  = ARGS.height / dev_h
        self.win_w  = int(dev_w * self.scale)
        self.win_h  = ARGS.height

        # ── Pygame window ─────────────────────────────────────────────────────
        pygame.init()
        self.screen = pygame.display.set_mode((self.win_w, self.win_h + STATUS_H))
        pygame.display.set_caption("keytap")

        # ── Components ────────────────────────────────────────────────────────
        self.capture  = CaptureManager(self.win_w, self.win_h,
                                       on_status=self._set_status)
        self.renderer = GridRenderer(self.win_w, self.win_h)
        self.renderer.init_fonts()

        self.elements = ElementManager(
            serial=ARGS.serial,
            scale=self.scale,
            on_status=self._set_status,
            on_tap=self._on_elem_tap,
        )
        self.clock = pygame.time.Clock()

        # ── App state ─────────────────────────────────────────────────────────
        self.cursor_row        = 0
        self.cursor_col        = 0
        self.input_buf         = []
        self._status           = "connecting..."
        self._raw_surf         = None    # latest pygame Surface from capture
        self._hier_highlight   = None    # (wx1, wy1, wx2, wy2)
        self._last_shift_t     = 0.0
        self._palette_result_q = queue.Queue()
        self._palette_proc     = None   # pre-warmed palette subprocess
        self._palette_ready    = False  # True once subprocess signals __ready__

        # Package list (for palette)
        self._packages         = []
        self._packages_ready   = False

        # Deeplink history
        self._deeplinks        = []

        # Status font
        self._status_font = self._try_font(11)

    # ── Font helper ───────────────────────────────────────────────────────────

    def _try_font(self, size):
        return fonts.load(size)

    # ── Status ────────────────────────────────────────────────────────────────

    def _set_status(self, msg):
        self._status = msg

    def _idle_status(self):
        if self.elements.active:
            self.elements.idle_status()
            return
        if CURSOR_MODE:
            r, c = self.cursor_row, self.cursor_col
            rl = string.ascii_uppercase[r] if r < 26 else str(r + 1)
            cl = string.ascii_uppercase[c]
            self._set_status(
                f"cursor: {rl}{cl} (row {r+1}, col {c+1})"
                f"  |  arrows=move  Shift=jump{CURSOR_JUMP}  space=tap"
                f"  w/s=scroll  b=back  h=home  r=recents  Esc=quit"
            )
        else:
            buf = ''.join(self.input_buf).upper()
            cols_r = string.ascii_uppercase[:ARGS.cols]
            rows_r = string.ascii_uppercase[:min(ARGS.rows, 26)]
            if len(buf) == 1:
                self._set_status(
                    f"row {buf} selected  ->  type col ({cols_r[0]}-{cols_r[-1]})"
                    f"  |  Backspace=cancel")
            else:
                self._set_status(
                    f"type row ({rows_r[0]}-{rows_r[-1]})"
                    f" then col ({cols_r[0]}-{cols_r[-1]})  |  Esc=quit")

    # ── Coordinate helpers ────────────────────────────────────────────────────

    def _cell_to_dev_rc(self, r, c):
        cw = self.dev_w / ARGS.cols
        ch = self.dev_h / ARGS.rows
        return int(c * cw + cw / 2), int(r * ch + ch / 2)

    def _label_to_dev(self, label):
        r = string.ascii_uppercase.index(label[0].upper())
        c = string.ascii_uppercase.index(label[1].upper())
        if r >= ARGS.rows or c >= ARGS.cols:
            raise ValueError("out of range")
        return self._cell_to_dev_rc(r, c)

    # ── ADB actions ───────────────────────────────────────────────────────────

    def _tap(self, dx, dy):
        actions.tap(dx, dy, on_status=self._set_status)

    def _keyevent(self, code, label=""):
        actions.keyevent(code, label, on_status=self._set_status)

    def _scroll(self, direction):
        cx, cy = self._cell_to_dev_rc(self.cursor_row, self.cursor_col)
        actions.scroll(cx, cy, self.dev_h, direction, on_status=self._set_status)

    def _on_elem_tap(self, cx, cy, hint):
        self._set_status(f"tap '{hint}' ({cx},{cy})")
        actions.tap(cx, cy)

    # ── Key handling ──────────────────────────────────────────────────────────

    def _handle_key(self, event):
        """Returns False to quit, True to continue."""
        sym  = event.key
        char = event.unicode.upper() if event.unicode else ""
        cl   = char.lower()
        mods = pygame.key.get_mods()
        step = CURSOR_JUMP if (mods & pygame.KMOD_SHIFT) else 1

        # Quit
        if sym == pygame.K_ESCAPE:
            return False

        # Double-shift → palette
        if sym in (pygame.K_LSHIFT, pygame.K_RSHIFT):
            now = time.time()
            if now - self._last_shift_t < 0.4:
                self._open_palette()
                self._last_shift_t = 0.0
            else:
                self._last_shift_t = now
            return True

        # Element mode toggle (priority)
        if cl == kb.element_mode:
            if self.elements.active:
                self.elements.exit()
            else:
                self.elements.enter(ref_surf=self._raw_surf)
            self._idle_status()
            return True

        if CURSOR_MODE:
            self._handle_cursor(sym, char, cl, step)
        else:
            self._handle_label(sym, char, cl)

        return True

    def _handle_cursor(self, sym, char, cl, step):
        """Arrow-key cursor mode."""
        if self.elements.active:
            if sym == pygame.K_TAB:
                self.elements.toggle_bounds()
            elif sym == pygame.K_BACKSPACE:
                self.elements.clear_buf()
            elif sym == pygame.K_UP:
                self.cursor_row = max(0, self.cursor_row - step)
            elif sym == pygame.K_DOWN:
                self.cursor_row = min(ARGS.rows - 1, self.cursor_row + step)
            elif sym == pygame.K_LEFT:
                self.cursor_col = max(0, self.cursor_col - step)
            elif sym == pygame.K_RIGHT:
                self.cursor_col = min(ARGS.cols - 1, self.cursor_col + step)
            elif sym == pygame.K_SPACE:
                dx, dy = self._cell_to_dev_rc(self.cursor_row, self.cursor_col)
                self._tap(dx, dy)
            elif cl == kb.back:
                self._keyevent(4, "back")
            elif cl == kb.home:
                self._keyevent(3, "home")
            elif cl == kb.recents:
                self._keyevent(187, "recents")
            elif cl == kb.scroll_up:
                self._scroll('up')
            elif cl == kb.scroll_down:
                self._scroll('down')
            else:
                self.elements.handle_char(cl)
            self._idle_status()
            return

        if sym == pygame.K_UP:
            self.cursor_row = max(0, self.cursor_row - step)
        elif sym == pygame.K_DOWN:
            self.cursor_row = min(ARGS.rows - 1, self.cursor_row + step)
        elif sym == pygame.K_LEFT:
            self.cursor_col = max(0, self.cursor_col - step)
        elif sym == pygame.K_RIGHT:
            self.cursor_col = min(ARGS.cols - 1, self.cursor_col + step)
        elif sym == pygame.K_SPACE:
            dx, dy = self._cell_to_dev_rc(self.cursor_row, self.cursor_col)
            self._tap(dx, dy)
            return
        elif cl == kb.back:
            self._keyevent(4, "back"); return
        elif cl == kb.home:
            self._keyevent(3, "home"); return
        elif cl == kb.recents:
            self._keyevent(187, "recents"); return
        elif cl == kb.scroll_up:
            self._scroll('up'); return
        elif cl == kb.scroll_down:
            self._scroll('down'); return
        else:
            return
        self._idle_status()

    def _handle_label(self, sym, char, cl):
        """2-letter label typing mode."""
        if sym == pygame.K_BACKSPACE:
            if self.input_buf:
                self.input_buf.pop()
                self._idle_status()
            return
        if not char.isalpha():
            return
        self.input_buf.append(char)
        if len(self.input_buf) == 1:
            self._idle_status()
            return
        label = ''.join(self.input_buf)
        self.input_buf.clear()
        try:
            dx, dy = self._label_to_dev(label)
            self._tap(dx, dy)
        except ValueError:
            self._set_status(f"'{label}' out of range")

    # ── Palette ───────────────────────────────────────────────────────────────

    def _open_palette(self):
        threading.Thread(target=self._fetch_packages, daemon=True).start()
        data = {
            "mode":               "palette",
            "packages":           self._packages,
            "clipboard":          self._read_clipboard(),
            "deeplinks":          self._deeplinks,
            "serial":             ARGS.serial,
            "adb_path":           ADB_PATH,
            "capture_bitrate":    self.capture.bitrate,
            "capture_low_latency": self.capture.low_latency,
        }
        if self._palette_proc and self._palette_ready:
            # Fast path: pre-warmed subprocess already has Python + tkinter loaded.
            # Just send data and it shows the window immediately.
            payload = (json.dumps(data) + "\n").encode()
            try:
                self._palette_proc.stdin.write(payload)
                self._palette_proc.stdin.flush()
                return
            except Exception:
                # Process died; fall through to spawn a fresh one.
                self._palette_proc  = None
                self._palette_ready = False
        threading.Thread(target=self._run_proc, args=(data,), daemon=True).start()

    def _run_proc(self, data, emit_result=True):
        payload = (json.dumps(data) + "\n").encode()
        try:
            proc = subprocess.Popen(
                [sys.executable, _PROC],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            proc.stdin.write(payload)
            proc.stdin.close()
            threading.Thread(
                target=self._read_proc_hints, args=(proc.stderr,), daemon=True
            ).start()
            stdout = proc.stdout.read()
            proc.wait(timeout=600)
            self._hier_highlight = None
            if emit_result:
                result = json.loads(stdout.decode().strip()) if stdout.strip() else None
                self._palette_result_q.put(result)
        except Exception as e:
            self._hier_highlight = None
            self._set_status(f"palette error: {e}")

    def _read_proc_hints(self, stderr):
        for raw in stderr:
            line = raw.decode().strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if msg.get("action") == "tap":
                    actions.tap(msg["cx"], msg["cy"])
                else:
                    bounds = msg.get("hover")
                    if bounds:
                        s = self.scale
                        x1, y1, x2, y2 = bounds
                        self._hier_highlight = (
                            int(x1 * s), int(y1 * s), int(x2 * s), int(y2 * s)
                        )
                    else:
                        self._hier_highlight = None
            except Exception:
                print(f"[proc stderr] {line}", flush=True)

    def _read_clipboard(self):
        try:
            return subprocess.run(
                ["pbpaste"], capture_output=True, text=True, timeout=1
            ).stdout
        except Exception:
            return ""

    # ── Palette pre-warming ───────────────────────────────────────────────────

    def _preload_palette(self):
        """Spawn palette subprocess hidden. Python + tkinter load in background.
        When user triggers palette, the process is already warm — just send data."""
        try:
            cmd = [sys.executable, _PROC, "--persistent"]
            if ARGS.serial:
                cmd += ["--serial", ARGS.serial]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._palette_proc = proc
            threading.Thread(
                target=self._read_persistent_hints, args=(proc.stderr,), daemon=True
            ).start()
            threading.Thread(
                target=self._read_persistent_results, args=(proc.stdout,), daemon=True
            ).start()
        except Exception as e:
            self._set_status(f"palette preload failed: {e}")

    def _read_persistent_hints(self, stderr):
        """Read stderr from the persistent palette process.
        Handles the __ready__ signal and live hints (hover, tap)."""
        for raw in stderr:
            line = raw.decode().strip()
            if not line:
                continue
            if line == "__ready__":
                self._palette_ready = True
                continue
            try:
                msg = json.loads(line)
                if msg.get("action") == "tap":
                    actions.tap(msg["cx"], msg["cy"])
                else:
                    bounds = msg.get("hover")
                    if bounds:
                        s = self.scale
                        x1, y1, x2, y2 = bounds
                        self._hier_highlight = (
                            int(x1 * s), int(y1 * s), int(x2 * s), int(y2 * s)
                        )
                    else:
                        self._hier_highlight = None
            except Exception:
                print(f"[palette stderr] {line}", flush=True)

    def _read_persistent_results(self, stdout):
        """Read results line-by-line from the persistent palette process."""
        for raw in stdout:
            try:
                result = json.loads(raw.decode().strip())
                self._hier_highlight = None
                self._palette_result_q.put(result)  # enqueue even None (close with no action)
            except Exception:
                pass

    def _on_palette_action(self, result):
        t = result.get('type')

        if t == 'close':
            return

        if t == 'tap':
            actions.tap(result['cx'], result['cy'], on_status=self._set_status)

        elif t == 'install-apk':
            path = result.get('path', '')
            if path:
                actions.install_apk(path, on_status=self._set_status)

        elif t == 'input-text':
            actions.input_text(result['text'], on_status=self._set_status)

        elif t == 'clipboard':
            text = result.get('value', '')
            if text:
                actions.input_text(text, on_status=self._set_status)
            else:
                self._set_status("clipboard empty")

        elif t == 'deeplink':
            url = result.get('value', '')
            actions.launch_deeplink(url, on_status=self._set_status)
            self._add_deeplink(url)

        elif t == 'theme-changed':
            self._set_status(f"theme set to {result.get('theme', '?')} – reopen palette to apply")

        elif t == 'capture-settings':
            import settings as _settings_mod
            updates = {k: result[k] for k in ('bitrate', 'low_latency') if k in result}
            if updates:
                _settings_mod.save_section('capture', updates)
                self.capture.restart_with_settings(**updates)
                mbps = int(self.capture.bitrate) // 1_000_000
                ll = " + low latency" if self.capture.low_latency else ""
                self._set_status(f"capture: {mbps}Mbps{ll} – restarting stream...")

        elif t == 'app-action':
            self._dispatch_app_action(result['pkg'], result['action'])

    def _dispatch_app_action(self, pkg, action):
        self._set_status(f"{action} {pkg}")
        if action == "launch":
            actions.launch(pkg, on_status=self._set_status)
        elif action == "force-stop":
            actions.force_stop(pkg, on_status=self._set_status)
        elif action == "clear-data":
            actions.clear_data(pkg, on_status=self._set_status)
        elif action == "uninstall":
            actions.uninstall(pkg, on_status=self._set_status)

    def _refocus_window(self):
        """Return OS focus to the pygame window after palette closes.

        Uses ObjC runtime directly (libobjc.dylib) — always present on macOS,
        no PyObjC install needed. No-op on non-macOS.
        """
        try:
            import ctypes
            import ctypes.util
            if sys.platform != 'darwin':
                return
            libobjc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc'))
            libobjc.objc_getClass.restype = ctypes.c_void_p
            libobjc.sel_registerName.restype = ctypes.c_void_p
            libobjc.objc_msgSend.restype = ctypes.c_void_p
            libobjc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            app = libobjc.objc_msgSend(
                libobjc.objc_getClass(b'NSApplication'),
                libobjc.sel_registerName(b'sharedApplication'),
            )
            # activateIgnoringOtherApps: takes a BOOL — use typed wrapper
            fn = ctypes.CFUNCTYPE(
                None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool
            )(libobjc.objc_msgSend)
            fn(app, libobjc.sel_registerName(b'activateIgnoringOtherApps:'), True)
        except Exception:
            pass

    # ── Deeplink history ──────────────────────────────────────────────────────

    _DEEPLINK_PATH = os.path.expanduser("~/.keytap_deeplinks.json")

    def _load_deeplinks(self):
        try:
            with open(self._DEEPLINK_PATH) as f:
                self._deeplinks = json.load(f)
        except Exception:
            self._deeplinks = []

    def _add_deeplink(self, url):
        if url in self._deeplinks:
            self._deeplinks.remove(url)
        self._deeplinks.insert(0, url)
        self._deeplinks = self._deeplinks[:20]
        threading.Thread(target=self._save_deeplinks, daemon=True).start()

    def _save_deeplinks(self):
        try:
            with open(self._DEEPLINK_PATH, "w") as f:
                json.dump(self._deeplinks, f)
        except Exception:
            pass

    # ── Package list ──────────────────────────────────────────────────────────

    def _fetch_packages(self):
        result = adb("shell", "pm", "list", "packages")
        self._packages = sorted(
            line.strip()[len("package:"):]
            for line in result.stdout.decode().splitlines()
            if line.strip().startswith("package:")
        )
        self._packages_ready = True

    # ── Draw ──────────────────────────────────────────────────────────────────

    def _draw(self):
        self.screen.fill((0, 0, 0))

        if self._raw_surf is not None:
            frame = self._raw_surf.copy()
            elems = self.elements.elements_for_renderer() if self.elements.active else None
            composited = self.renderer.composite(
                frame,
                self.cursor_row, self.cursor_col,
                self.input_buf,
                elements=elems,
                highlight_bounds=self._hier_highlight,
            )
            self.screen.blit(composited, (0, 0))

        self._draw_status()
        pygame.display.flip()

    def _draw_status(self):
        bar_y = self.win_h
        pygame.draw.rect(self.screen, (26, 26, 26),
                         (0, bar_y, self.win_w, STATUS_H))
        if self._status_font:
            t = self._status_font.render(self._status[:120], True, (136, 136, 136))
            self.screen.blit(t, (8, bar_y + (STATUS_H - t.get_height()) // 2))

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        from settings import save_defaults
        save_defaults()
        self._load_deeplinks()
        threading.Thread(target=self._fetch_packages, daemon=True).start()
        self._preload_palette()
        self.capture.start()

        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

                elif event.type == pygame.KEYDOWN:
                    if not self._handle_key(event):
                        running = False

            # Drain all queued frames, keep only the latest to stay in sync
            raw_frame = None
            try:
                while True:
                    raw_frame = self.capture.frame_q.get_nowait()
            except queue.Empty:
                pass
            if raw_frame is not None:
                # Direct bytes -> Surface: skips PIL tobytes() copy, ~2-10ms faster per frame
                self._raw_surf = pygame.image.frombytes(
                    raw_frame, (self.capture.win_w, self.capture.win_h), 'RGB'
                )
                self._idle_status()
                if self.elements.active and not self.elements.loading:
                    if self.elements.check_screen_change(self._raw_surf):
                        self.elements.enter(ref_surf=self._raw_surf)

            # Process palette subprocess results
            try:
                result = self._palette_result_q.get_nowait()
                if result:
                    self._on_palette_action(result)
                self._refocus_window()
            except queue.Empty:
                pass

            self._draw()
            self.clock.tick(120)

        self.capture.stop()
        pygame.quit()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not ARGS.serial:
        devices = list_devices()
        if not devices:
            print("No devices connected. Please connect a device and try again.")
            sys.exit(1)
        elif len(devices) == 1:
            ARGS.serial = devices[0]
            print(f"Device: {ARGS.serial}")
        else:
            print("Multiple devices found:")
            for i, s in enumerate(devices):
                print(f"  [{i + 1}] {s}")
            while True:
                try:
                    choice = int(input(f"Select device [1-{len(devices)}]: ")) - 1
                    if 0 <= choice < len(devices):
                        ARGS.serial = devices[choice]
                        break
                    print(f"Enter a number between 1 and {len(devices)}")
                except (ValueError, EOFError):
                    print(f"Enter a number between 1 and {len(devices)}")
                except KeyboardInterrupt:
                    print()
                    sys.exit(0)

    print("connecting...")
    try:
        dev_w, dev_h = get_device_size()
    except Exception as e:
        print(f"ADB error: {e}")
        print("Is device connected? Run: adb devices")
        sys.exit(1)

    print(f"device {dev_w}x{dev_h} | mirror {int(dev_w * ARGS.height / dev_h)}x{ARGS.height}")
    KeyTap(dev_w, dev_h).run()
