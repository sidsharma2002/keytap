from utils import adb_shell

_OPTIONS = [
    {
        "id": "animations",
        "label": "Animations",
        "read_ns": "global", "read_key": "window_animation_scale",
        "enable_cmds": [
            "settings put global window_animation_scale 1",
            "settings put global transition_animation_scale 1",
            "settings put global animator_duration_scale 1",
        ],
        "disable_cmds": [
            "settings put global window_animation_scale 0",
            "settings put global transition_animation_scale 0",
            "settings put global animator_duration_scale 0",
        ],
        "default_on": True,
    },
    {
        "id": "show_touches",
        "label": "Show Touches",
        "read_ns": "system", "read_key": "show_touches",
        "on_val": "1",
        "enable_cmds": ["settings put system show_touches 1"],
        "disable_cmds": ["settings put system show_touches 0"],
        "default_on": False,
    },
    {
        "id": "pointer_location",
        "label": "Pointer Location",
        "read_ns": "system", "read_key": "pointer_location",
        "on_val": "1",
        "enable_cmds": ["settings put system pointer_location 1"],
        "disable_cmds": ["settings put system pointer_location 0"],
        "default_on": False,
    },
    {
        "id": "stay_awake",
        "label": "Stay Awake",
        "read_ns": "global", "read_key": "stay_on_while_plugged_in",
        "on_val": "3",
        "enable_cmds": ["settings put global stay_on_while_plugged_in 3"],
        "disable_cmds": ["settings put global stay_on_while_plugged_in 0"],
        "default_on": False,
    },
    {
        "id": "finish_activities",
        "label": "Don't Keep Activities",
        "read_ns": "global", "read_key": "always_finish_activities",
        "on_val": "1",
        "enable_cmds": ["settings put global always_finish_activities 1"],
        "disable_cmds": ["settings put global always_finish_activities 0"],
        "default_on": False,
    },
]

_BY_LABEL = {o["label"]: o for o in _OPTIONS}


def _is_enabled(opt, serial):
    raw = adb_shell(f"settings get {opt['read_ns']} {opt['read_key']}", serial).strip()
    if raw in ("", "null"):
        return opt["default_on"]
    if opt["id"] == "animations":
        try:
            return float(raw) != 0.0
        except ValueError:
            return opt["default_on"]
    return raw == opt["on_val"]


def fetch(serial=None):
    """Return [(label, 'ON'|'OFF')] for all dev options."""
    return [(opt["label"], "ON" if _is_enabled(opt, serial) else "OFF") for opt in _OPTIONS]


def toggle_by_label(label, serial=None):
    """Toggle option by label. Returns new state string."""
    opt = _BY_LABEL.get(label)
    if not opt:
        return "?"
    enabled = _is_enabled(opt, serial)
    for cmd in (opt["disable_cmds"] if enabled else opt["enable_cmds"]):
        adb_shell(cmd, serial)
    return "OFF" if enabled else "ON"
