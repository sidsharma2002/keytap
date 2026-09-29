"""
Data structures for recorded action sequences.
"""
import json
import os
import shutil
import time
from dataclasses import dataclass, field, asdict
from typing import List, Optional

RECORDINGS_DIR = os.path.expanduser("~/.keytap/recordings")


def _compact(d):
    """Strip None values from a dict (shallow)."""
    return {k: v for k, v in d.items() if v is not None}


@dataclass
class ElementSelector:
    resource_id: str = ""
    content_desc: str = ""
    text: str = ""
    class_name: str = ""


@dataclass
class Action:
    t: int           # ms since recording start
    type: str        # tap | element_tap | swipe | input_text | launch | launch_deeplink | keyevent
    # tap
    x: Optional[int] = None
    y: Optional[int] = None
    # element_tap
    selector: Optional[ElementSelector] = None
    fallback_coords: Optional[List[int]] = None
    reliable: bool = True
    # swipe
    x1: Optional[int] = None
    y1: Optional[int] = None
    x2: Optional[int] = None
    y2: Optional[int] = None
    duration: Optional[int] = None
    # input_text
    text: Optional[str] = None
    # launch
    package: Optional[str] = None
    # launch_deeplink
    url: Optional[str] = None
    # keyevent
    keycode: Optional[int] = None
    label: Optional[str] = None

    def to_dict(self):
        d = asdict(self)
        if self.selector:
            d["selector"] = asdict(self.selector)
        return _compact(d)


@dataclass
class Recording:
    name: str
    recorded_at: str
    serial: str
    device_resolution: List[int]
    actions: List[Action] = field(default_factory=list)
    network_flows: List[dict] = field(default_factory=list)
    network_host_tokens: dict = field(default_factory=dict)
    # correlated per-frame timeline (actions + logcat + network, sorted by t)
    timeline: List[dict] = field(default_factory=list)
    video_path: Optional[str] = None   # relative path to screen.mp4 inside recording dir

    def save(self, screenshots: Optional[dict] = None,
             video_src_path: Optional[str] = None) -> str:
        """
        Saves recording as a directory:
          ~/.keytap/recordings/{name}_{ts}/
            actions.json
            network.json        (only if network_flows non-empty)
            timeline.json       (only if timeline non-empty)
            screen.mp4          (only if video_src_path provided)
        Returns the directory path.
        """
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in self.name)
        dir_path = os.path.join(RECORDINGS_DIR, f"{safe}_{ts}")
        os.makedirs(dir_path, exist_ok=True)

        # Move video from temp location into recording dir
        if video_src_path and os.path.exists(video_src_path):
            dest = os.path.join(dir_path, "screen.mp4")
            shutil.move(video_src_path, dest)
            self.video_path = "screen.mp4"

        actions_data = {
            "name": self.name,
            "recorded_at": self.recorded_at,
            "serial": self.serial,
            "device_resolution": self.device_resolution,
            "actions": [a.to_dict() for a in self.actions],
        }
        if self.video_path:
            actions_data["video_path"] = self.video_path
        with open(os.path.join(dir_path, "actions.json"), "w") as f:
            json.dump(actions_data, f, indent=2)

        if self.network_flows:
            network_data = {
                "host_tokens": self.network_host_tokens,
                "flows": self.network_flows,
            }
            with open(os.path.join(dir_path, "network.json"), "w") as f:
                json.dump(network_data, f, indent=2)

        if self.timeline:
            with open(os.path.join(dir_path, "timeline.json"), "w") as f:
                json.dump({"events": self.timeline}, f, indent=2)

        return dir_path

    @classmethod
    def load(cls, path: str) -> "Recording":
        """
        Loads a recording from:
          - a directory (new format): reads actions.json + network.json
          - a .json file (legacy format): reads single file
        """
        if os.path.isdir(path):
            return cls._load_dir(path)
        return cls._load_legacy(path)

    @classmethod
    def _load_dir(cls, dir_path: str) -> "Recording":
        with open(os.path.join(dir_path, "actions.json")) as f:
            data = json.load(f)

        network_path = os.path.join(dir_path, "network.json")
        network_flows: List[dict] = []
        network_host_tokens: dict = {}
        if os.path.exists(network_path):
            with open(network_path) as f:
                net = json.load(f)
            network_flows = net.get("flows", [])
            network_host_tokens = net.get("host_tokens", {})

        return cls(
            name=data["name"],
            recorded_at=data["recorded_at"],
            serial=data.get("serial", ""),
            device_resolution=data.get("device_resolution", [0, 0]),
            actions=cls._parse_actions(data.get("actions", [])),
            network_flows=network_flows,
            network_host_tokens=network_host_tokens,
        )

    @classmethod
    def _load_legacy(cls, path: str) -> "Recording":
        with open(path) as f:
            data = json.load(f)
        return cls(
            name=data["name"],
            recorded_at=data["recorded_at"],
            serial=data.get("serial", ""),
            device_resolution=data.get("device_resolution", [0, 0]),
            actions=cls._parse_actions(data.get("actions", [])),
            network_flows=data.get("network_flows", []),
            network_host_tokens=data.get("network_host_tokens", {}),
        )

    @staticmethod
    def _parse_actions(raw: list) -> "List[Action]":
        actions = []
        for a in raw:
            sel = ElementSelector(**a["selector"]) if a.get("selector") else None
            actions.append(Action(
                t=a["t"],
                type=a["type"],
                x=a.get("x"), y=a.get("y"),
                selector=sel,
                fallback_coords=a.get("fallback_coords"),
                reliable=a.get("reliable", True),
                x1=a.get("x1"), y1=a.get("y1"),
                x2=a.get("x2"), y2=a.get("y2"),
                duration=a.get("duration"),
                text=a.get("text"),
                package=a.get("package"),
                url=a.get("url"),
                keycode=a.get("keycode"),
                label=a.get("label"),
            ))
        return actions

    @classmethod
    def list_all(cls) -> List[dict]:
        """Return [{path, name, recorded_at, action_count, has_network}] sorted newest first."""
        if not os.path.isdir(RECORDINGS_DIR):
            return []
        results = []
        for entry in os.listdir(RECORDINGS_DIR):
            full = os.path.join(RECORDINGS_DIR, entry)
            try:
                if os.path.isdir(full):
                    actions_path = os.path.join(full, "actions.json")
                    if not os.path.exists(actions_path):
                        continue
                    with open(actions_path) as f:
                        d = json.load(f)
                    results.append({
                        "path": full,
                        "name": d.get("name", entry),
                        "recorded_at": d.get("recorded_at", ""),
                        "action_count": len(d.get("actions", [])),
                        "has_network": os.path.exists(os.path.join(full, "network.json")),
                    })
                elif entry.endswith(".json"):
                    # legacy single-file recording
                    with open(full) as f:
                        d = json.load(f)
                    results.append({
                        "path": full,
                        "name": d.get("name", entry),
                        "recorded_at": d.get("recorded_at", ""),
                        "action_count": len(d.get("actions", [])),
                        "has_network": bool(d.get("network_flows")),
                    })
            except Exception:
                pass
        results.sort(key=lambda r: r["recorded_at"], reverse=True)
        return results


def action_label(action: "Action") -> str:
    """Short human-readable label for an action (status bar, step overlay, viewers)."""
    t = action.type
    if t == "element_tap":
        sel = action.selector
        name = sel and (sel.resource_id or sel.content_desc or sel.text)
        if name and "/" in name:
            name = name.split("/")[-1]  # strip package prefix from resource_id
        return f"element_tap: {name or '?'}"
    elif t == "tap":
        return f"tap ({action.x}, {action.y})"
    elif t == "swipe":
        return f"swipe ({action.x1},{action.y1})->({action.x2},{action.y2})"
    elif t == "input_text":
        preview = repr((action.text or "")[:20])
        return f"input_text: {preview}"
    elif t == "launch":
        pkg = (action.package or "").split(".")[-1]
        return f"launch: {pkg}"
    elif t == "launch_deeplink":
        return f"deeplink: {(action.url or '')[:30]}"
    elif t == "keyevent":
        return f"keyevent: {action.label or action.keycode}"
    elif t == "wait":
        return f"wait: {action.duration}ms"
    return t
