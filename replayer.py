"""
ActionReplayer: executes a Recording on the connected device.

Modes:
  timed  - honor recorded timestamps (real-time replay)
  fast   - fixed 300ms gap between each action
  step   - advance manually by calling advance()
"""
import threading
import time

import actions
from recording import Action, ElementSelector, Recording


class ActionReplayer:
    FAST_DELAY_MS = 300

    def __init__(self):
        self.active = False
        self.mode = "timed"
        self._stop_event = threading.Event()
        self._step_event = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, recording: Recording, mode: str, serial: str,
              on_status, on_done, device_resolution=None):
        self.mode = mode
        self.active = True
        self._stop_event.clear()
        self._step_event.clear()
        threading.Thread(
            target=self._run,
            args=(recording, serial, on_status, on_done, device_resolution),
            daemon=True,
        ).start()

    def advance(self):
        """Unblock one step in step mode."""
        self._step_event.set()

    def stop(self):
        self._stop_event.set()
        self._step_event.set()  # unblock a waiting step
        self.active = False

    # ── Replay loop ───────────────────────────────────────────────────────────

    def _run(self, recording, serial, on_status, on_done, device_resolution):
        total = len(recording.actions)

        if device_resolution and recording.device_resolution != list(device_resolution):
            on_status(
                f"[WARN] recorded on {recording.device_resolution}, "
                f"device is {list(device_resolution)} — coords may miss"
            )

        prev_t = 0
        for i, action in enumerate(recording.actions):
            if self._stop_event.is_set():
                on_status("replay stopped")
                self.active = False
                return

            on_status(f"[REPLAY {i + 1}/{total}] {action.type}")
            self._wait_between(action.t, prev_t)
            prev_t = action.t

            ok, error = self._execute(action, serial)
            if not ok:
                on_status(f"[REPLAY ERROR] step {i + 1}: {error}")
                self.active = False
                on_done(success=False, step=i + 1, error=error)
                return

        self.active = False
        on_status(f"replay done  ({total} actions)")
        on_done(success=True, step=total, error=None)

    def _wait_between(self, action_t_ms: int, prev_t_ms: int):
        if self.mode == "step":
            self._step_event.wait()
            self._step_event.clear()
        elif self.mode == "fast":
            time.sleep(self.FAST_DELAY_MS / 1000)
        else:  # timed
            gap_s = (action_t_ms - prev_t_ms) / 1000
            if gap_s > 0:
                time.sleep(min(gap_s, 10))  # cap at 10s to guard against huge pauses

    def _execute(self, action: Action, serial: str):
        """Dispatch one action. Returns (success, error_msg)."""
        t = action.type
        if t == "tap":
            actions.tap(action.x, action.y)
        elif t == "element_tap":
            return self._execute_element_tap(action, serial)
        elif t == "swipe":
            actions.swipe(action.x1, action.y1, action.x2, action.y2,
                          duration=action.duration or 300)
        elif t == "input_text":
            actions.input_text(action.text)
        elif t == "launch":
            actions.launch(action.package)
        elif t == "launch_deeplink":
            actions.launch_deeplink(action.url)
        elif t == "keyevent":
            actions.keyevent(action.keycode, action.label or "")
        else:
            return False, f"unknown action type: {t}"
        return True, None

    def _execute_element_tap(self, action: Action, serial: str):
        try:
            import uiautomator2 as u2
            dev = u2.connect(serial) if serial else u2.connect()
            el = self._find_element(dev, action.selector)
            if el is not None:
                cx, cy = el.center()
                actions.tap(cx, cy)
                return True, None
        except Exception as e:
            # Fall through to fallback if available
            if not action.reliable and action.fallback_coords:
                cx, cy = action.fallback_coords
                actions.tap(cx, cy)
                return True, None
            return False, f"element lookup failed: {e}"

        # Element not found
        if not action.reliable and action.fallback_coords:
            cx, cy = action.fallback_coords
            actions.tap(cx, cy)
            return True, None

        sel = action.selector
        desc = sel.resource_id or sel.content_desc or sel.text or "?"
        return False, f"element '{desc}' not found"

    def _find_element(self, dev, selector: ElementSelector):
        """Try selector fields in priority order. Returns u2 UiObject or None."""
        checks = [
            (selector.resource_id, lambda v: dev(resourceId=v)),
            (selector.content_desc, lambda v: dev(description=v)),
            (selector.text,         lambda v: dev(text=v)),
        ]
        for value, factory in checks:
            if not value:
                continue
            try:
                el = factory(value)
                if el.exists:
                    return el
            except Exception:
                continue
        return None
