import sys
import time as _time

from utils import adb_shell

# type="toggle"  — binary ON/OFF via settings put
# type="setprop" — binary ON/OFF via setprop (needs app restart, label ends with *)
# type="cycle"   — cycles through discrete values

_OPTIONS = [
    # ── Network / Connectivity ───────────────────────────────────────────────
    {
        "id": "wifi",
        "label": "WiFi",
        "read_ns": "global", "read_key": "wifi_on",
        "on_val": "1",
        "enable_cmds":  ["svc wifi enable"],
        "disable_cmds": ["svc wifi disable"],
        "default_on": True,
    },
    {
        "id": "mobile_data",
        "label": "Mobile Data",
        "read_ns": "global", "read_key": "mobile_data",
        "on_val": "1",
        "enable_cmds":  ["svc data enable"],
        "disable_cmds": ["svc data disable"],
        "default_on": True,
    },
    {
        "id": "bluetooth",
        "label": "Bluetooth",
        "read_ns": "global", "read_key": "bluetooth_on",
        "on_val": "1",
        "enable_cmds":  ["svc bluetooth enable"],
        "disable_cmds": ["svc bluetooth disable"],
        "default_on": False,
    },
    # ── Display ──────────────────────────────────────────────────────────────
    {
        "id": "dark_mode",
        "label": "Dark Mode",
        "read_ns": "secure", "read_key": "ui_night_mode",
        "on_val": "2",
        "enable_cmds":  ["settings put secure ui_night_mode 2"],
        "disable_cmds": ["settings put secure ui_night_mode 1"],
        "default_on": False,
    },
    {
        "id": "font_scale",
        "label": "Font Scale",
        "type": "cycle",
        "read_ns": "system", "read_key": "font_scale",
        "cycle_vals": ["1.0", "1.15", "1.3", "2.0"],
        "default_val": "1.0",
        "cmd_tpl": "settings put system font_scale {}",
    },
    # ── Touch / Input ────────────────────────────────────────────────────────
    {
        "id": "show_touches",
        "label": "Show Touches",
        "read_ns": "system", "read_key": "show_touches",
        "on_val": "1",
        "enable_cmds":  ["settings put system show_touches 1"],
        "disable_cmds": ["settings put system show_touches 0"],
        "default_on": False,
    },
    {
        "id": "pointer_location",
        "label": "Pointer Location",
        "read_ns": "system", "read_key": "pointer_location",
        "on_val": "1",
        "enable_cmds":  ["settings put system pointer_location 1"],
        "disable_cmds": ["settings put system pointer_location 0"],
        "default_on": False,
    },
    # ── Developer ────────────────────────────────────────────────────────────
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
        "id": "stay_awake",
        "label": "Stay Awake",
        "read_ns": "global", "read_key": "stay_on_while_plugged_in",
        "on_val": "3",
        "enable_cmds":  ["settings put global stay_on_while_plugged_in 3"],
        "disable_cmds": ["settings put global stay_on_while_plugged_in 0"],
        "default_on": False,
    },
    {
        "id": "finish_activities",
        "label": "Don't Keep Activities",
        "read_ns": "global", "read_key": "always_finish_activities",
        "on_val": "1",
        "enable_cmds":  ["settings put global always_finish_activities 1"],
        "disable_cmds": ["settings put global always_finish_activities 0"],
        "default_on": False,
    },
    {
        "id": "force_rtl",
        "label": "Force RTL",
        "read_ns": "global", "read_key": "debug.force_rtl",
        "on_val": "1",
        "enable_cmds":  ["settings put global debug.force_rtl 1"],
        "disable_cmds": ["settings put global debug.force_rtl 0"],
        "default_on": False,
    },
    {
        "id": "mock_location",
        "label": "Mock Locations",
        "read_ns": "secure", "read_key": "allow_mock_location",
        "on_val": "1",
        "enable_cmds":  ["settings put secure allow_mock_location 1"],
        "disable_cmds": ["settings put secure allow_mock_location 0"],
        "default_on": False,
    },
    {
        "id": "high_contrast",
        "label": "High Contrast Text",
        "read_ns": "secure", "read_key": "high_text_contrast_enabled",
        "on_val": "1",
        "enable_cmds":  ["settings put secure high_text_contrast_enabled 1"],
        "disable_cmds": ["settings put secure high_text_contrast_enabled 0"],
        "default_on": False,
    },
    # ── GPU / Rendering  (* = needs app restart) ─────────────────────────────
    {
        "id": "gpu_overdraw",
        "label": "GPU Overdraw *",
        "type": "setprop",
        "prop": "debug.hwui.overdraw",
        "on_val": "show",
        "enable_cmds":  ["setprop debug.hwui.overdraw show"],
        "disable_cmds": ["setprop debug.hwui.overdraw false"],
        "default_on": False,
    },
    {
        "id": "layout_bounds",
        "label": "Layout Bounds *",
        "type": "setprop",
        "prop": "debug.layout",
        "on_val": "true",
        "enable_cmds":  ["setprop debug.layout true"],
        "disable_cmds": ["setprop debug.layout false"],
        "default_on": False,
    },
    {
        "id": "gpu_profile",
        "label": "Profile GPU Rendering *",
        "type": "setprop",
        "prop": "debug.hwui.profile",
        "on_val": "true",
        "enable_cmds":  ["setprop debug.hwui.profile true"],
        "disable_cmds": ["setprop debug.hwui.profile false"],
        "default_on": False,
    },
]

_BY_LABEL = {o["label"]: o for o in _OPTIONS}


def _is_enabled(opt, serial):
    t = opt.get("type", "toggle")
    _t0 = _time.time()
    if t == "setprop":
        raw = adb_shell(f"getprop {opt['prop']}", serial).strip()
        print(f"[DEBUG][dev_options] getprop {opt['prop']} -> {repr(raw)} ({_time.time()-_t0:.2f}s)", file=sys.stderr, flush=True)
        return raw == opt["on_val"]
    # toggle (settings-based)
    raw = adb_shell(f"settings get {opt['read_ns']} {opt['read_key']}", serial).strip()
    print(f"[DEBUG][dev_options] settings get {opt['read_ns']} {opt['read_key']} -> {repr(raw)} ({_time.time()-_t0:.2f}s)", file=sys.stderr, flush=True)
    if raw in ("", "null"):
        return opt.get("default_on", False)
    if opt["id"] == "animations":
        try:
            return float(raw) != 0.0
        except ValueError:
            return opt.get("default_on", False)
    return raw == opt.get("on_val", "1")


def fetch(serial=None):
    """Return [(label, state_str)] for all options."""
    _t0 = _time.time()
    print(f"[DEBUG][dev_options] fetch start, {len(_OPTIONS)} options", file=sys.stderr, flush=True)
    items = []
    for opt in _OPTIONS:
        t = opt.get("type", "toggle")
        if t == "cycle":
            raw = adb_shell(f"settings get {opt['read_ns']} {opt['read_key']}", serial).strip()
            if raw in ("", "null"):
                raw = opt["default_val"]
            state = f"{raw}x"
        else:
            state = "ON" if _is_enabled(opt, serial) else "OFF"
        items.append((opt["label"], state))
    print(f"[DEBUG][dev_options] fetch done in {_time.time()-_t0:.2f}s", file=sys.stderr, flush=True)
    return items


def toggle_by_label(label, serial=None):
    """Toggle or cycle option by label. Returns new state string."""
    opt = _BY_LABEL.get(label)
    if not opt:
        return "?"
    t = opt.get("type", "toggle")
    if t == "cycle":
        raw = adb_shell(f"settings get {opt['read_ns']} {opt['read_key']}", serial).strip()
        if raw in ("", "null"):
            raw = opt["default_val"]
        try:
            idx = opt["cycle_vals"].index(raw)
        except ValueError:
            idx = -1
        next_val = opt["cycle_vals"][(idx + 1) % len(opt["cycle_vals"])]
        adb_shell(opt["cmd_tpl"].format(next_val), serial)
        return f"{next_val}x"
    enabled = _is_enabled(opt, serial)
    for cmd in (opt["disable_cmds"] if enabled else opt["enable_cmds"]):
        adb_shell(cmd, serial)
    return "OFF" if enabled else "ON"
