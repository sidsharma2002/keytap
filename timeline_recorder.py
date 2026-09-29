"""
TimelineRecorder: correlated timeline of UI actions, logcat, and screenshots.

During recording:
  - Captures logcat in background (structured, filtered)
  - After each action, takes an async screenshot (non-blocking, 300ms settle delay)
  - Screenshots stored in memory as PNG bytes

On stop():
  - Returns (events, screenshots) where:
      events     = sorted list of {t, type, ...} dicts
      screenshots = {action_index: png_bytes}

Caller writes screenshots to disk at save time (path unknown until then).

Timeline event shapes:
  {t, type="action",      action_index, action_type, selector_hint}
  {t, type="log",         level, tag, msg}
  {t, type="network_req", method, url}
  {t, type="network_res", method, url, status, duration_ms}
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from adb import take_screencap
from logcat_capture import LogcatCapture

_LOG = logging.getLogger(__name__)


class TimelineRecorder:

    def __init__(self):
        self._logcat      = LogcatCapture()
        self._action_evts: List[dict] = []
        self._screenshots: Dict[int, bytes] = {}   # action_index -> png bytes
        self._lock        = threading.Lock()
        self._start_t     = 0.0
        self._active      = False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self, serial: Optional[str] = None, tags: Optional[List[str]] = None):
        with self._lock:
            self._action_evts.clear()
            self._screenshots.clear()
        self._start_t = time.time()
        self._active  = True
        self._logcat.start(serial=serial, tags=tags)
        _LOG.debug("timeline_recorder: started")

    def on_action(self, action_index: int, action: "Action"):
        """Call immediately after an action is appended to the recorder.

        action_index : 0-based index of the action in recorder's list
        action       : the Action dataclass just recorded
        """
        if not self._active:
            return

        t_ms = int((time.time() - self._start_t) * 1000)

        # Build a short hint for the timeline viewer
        hint = _selector_hint(action)

        evt = {
            "t":            t_ms,
            "type":         "action",
            "action_index": action_index,
            "action_type":  action.type,
            "hint":         hint,
            "screenshot":   None,   # relative path filled in at save time
        }
        with self._lock:
            self._action_evts.append(evt)

        # Async screenshot — does not block the recording flow
        threading.Thread(
            target=self._take_screenshot,
            args=(action_index,),
            daemon=True,
        ).start()

    def _take_screenshot(self, action_index: int):
        time.sleep(0.3)   # let UI settle after the tap/swipe
        try:
            png = take_screencap()
            if png:
                with self._lock:
                    self._screenshots[action_index] = png
                _LOG.debug("screenshot captured for action %d (%d bytes)", action_index, len(png))
        except Exception as e:
            _LOG.debug("screenshot failed for action %d: %s", action_index, e)

    def stop(self, network_flows: Optional[List[dict]] = None
             ) -> Tuple[List[dict], Dict[int, bytes]]:
        """Stop capture. Returns (timeline_events, screenshots_dict)."""
        self._active = False
        log_events   = self._logcat.stop()

        with self._lock:
            action_events = list(self._action_evts)
            screenshots   = dict(self._screenshots)

        net_events = _network_events(network_flows or [])

        all_events = action_events + log_events + net_events
        all_events.sort(key=lambda e: e["t"])

        _LOG.debug(
            "timeline_recorder: %d action / %d log / %d net events",
            len(action_events), len(log_events), len(net_events),
        )
        return all_events, screenshots


# ── Helpers ───────────────────────────────────────────────────────────────

def _selector_hint(action) -> str:
    """Short label for an action event in the timeline."""
    t = action.type
    if t == "element_tap":
        sel = action.selector
        if sel:
            name = sel.resource_id or sel.content_desc or sel.text
            if name and "/" in name:
                name = name.split("/")[-1]
            return name or "?"
        return "?"
    elif t == "tap":
        return f"({action.x},{action.y})"
    elif t == "swipe":
        return f"({action.x1},{action.y1})->({action.x2},{action.y2})"
    elif t == "input_text":
        return repr((action.text or "")[:20])
    elif t == "keyevent":
        return action.label or str(action.keycode)
    elif t == "wait":
        return f"{action.duration}ms"
    elif t == "launch":
        return (action.package or "").split(".")[-1]
    return t


def _network_events(flows: List[dict]) -> List[dict]:
    """Convert network flow dicts to timeline events.

    Flows are expected to have t_request_ms / t_response_ms if available.
    Flows without timestamps are skipped (can't place on timeline).
    """
    events = []
    for flow in flows:
        t_req  = flow.get("t_request_ms")
        t_resp = flow.get("t_response_ms")
        method = flow.get("method", "?")
        url    = flow.get("url", "?")
        status = flow.get("response_status")

        if t_req is not None:
            events.append({
                "t":      t_req,
                "type":   "network_req",
                "method": method,
                "url":    url,
            })
        if t_resp is not None:
            events.append({
                "t":           t_resp,
                "type":        "network_res",
                "method":      method,
                "url":         url,
                "status":      status,
                "duration_ms": (t_resp - t_req) if t_req is not None else None,
            })
    return events
