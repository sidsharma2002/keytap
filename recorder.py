"""
ActionRecorder: captures user actions into a Recording.
Called explicitly at intent sites in app.py — no monkey-patching.
"""
import time

from recording import Action, ElementSelector, Recording


class ActionRecorder:

    def __init__(self):
        self.active = False
        self._actions: list = []
        self._start_ms: int = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        self.active = True
        self._actions = []
        self._start_ms = int(time.time() * 1000)

    def stop(self):
        self.active = False

    def save(self, name: str, serial: str, device_resolution) -> str:
        recording = Recording(
            name=name,
            recorded_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            serial=serial or "",
            device_resolution=list(device_resolution),
            actions=self._actions[:],
        )
        return recording.save()

    @property
    def action_count(self) -> int:
        return len(self._actions)

    # ── Record calls (no-ops when inactive) ───────────────────────────────────

    def record_tap(self, x: int, y: int):
        if self.active:
            self._actions.append(Action(t=self._ms(), type="tap", x=x, y=y))

    def record_element_tap(self, element: dict):
        if not self.active:
            return
        sel = ElementSelector(
            resource_id=element.get("resource_id", ""),
            content_desc=element.get("content_desc", ""),
            text=element.get("text", ""),
            class_name=element.get("class_name", ""),
        )
        reliable = bool(sel.resource_id or sel.content_desc or sel.text)
        self._actions.append(Action(
            t=self._ms(),
            type="element_tap",
            selector=sel,
            fallback_coords=[element["cx"], element["cy"]],
            reliable=reliable,
        ))

    def record_swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300):
        if self.active:
            self._actions.append(Action(
                t=self._ms(), type="swipe",
                x1=x1, y1=y1, x2=x2, y2=y2, duration=duration,
            ))

    def record_input_text(self, text: str):
        if self.active:
            self._actions.append(Action(t=self._ms(), type="input_text", text=text))

    def record_launch(self, package: str):
        if self.active:
            self._actions.append(Action(t=self._ms(), type="launch", package=package))

    def record_launch_deeplink(self, url: str):
        if self.active:
            self._actions.append(Action(t=self._ms(), type="launch_deeplink", url=url))

    def record_keyevent(self, keycode: int, label: str = ""):
        if self.active:
            self._actions.append(Action(
                t=self._ms(), type="keyevent", keycode=keycode, label=label,
            ))

    def record_wait(self, ms: int):
        if self.active:
            self._actions.append(Action(t=self._ms(), type="wait", duration=ms))

    # ── Internal ──────────────────────────────────────────────────────────────

    def _ms(self) -> int:
        return int(time.time() * 1000) - self._start_ms
