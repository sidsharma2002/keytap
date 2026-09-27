"""
Data structures for recorded action sequences.
"""
import json
import os
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

    def save(self) -> str:
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in self.name)
        path = os.path.join(RECORDINGS_DIR, f"{safe}_{ts}.json")
        data = {
            "name": self.name,
            "recorded_at": self.recorded_at,
            "serial": self.serial,
            "device_resolution": self.device_resolution,
            "actions": [a.to_dict() for a in self.actions],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return path

    @classmethod
    def load(cls, path: str) -> "Recording":
        with open(path) as f:
            data = json.load(f)
        actions = []
        for a in data.get("actions", []):
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
        return cls(
            name=data["name"],
            recorded_at=data["recorded_at"],
            serial=data.get("serial", ""),
            device_resolution=data.get("device_resolution", [0, 0]),
            actions=actions,
        )

    @classmethod
    def list_all(cls) -> List[dict]:
        """Return [{path, name, recorded_at}] sorted newest first."""
        if not os.path.isdir(RECORDINGS_DIR):
            return []
        results = []
        for fname in os.listdir(RECORDINGS_DIR):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(RECORDINGS_DIR, fname)
            try:
                with open(path) as f:
                    d = json.load(f)
                results.append({
                    "path": path,
                    "name": d.get("name", fname),
                    "recorded_at": d.get("recorded_at", ""),
                    "action_count": len(d.get("actions", [])),
                })
            except Exception:
                pass
        results.sort(key=lambda r: r["recorded_at"], reverse=True)
        return results
