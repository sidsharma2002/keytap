import re
import xml.etree.ElementTree as ET

try:
    import uiautomator2 as u2
    HAS_U2 = True
except ImportError:
    HAS_U2 = False

# 'e' is reserved for exit-element-mode
LABELS = [c for c in 'abcdfghijklmnopqrstuvwxyz']  # 25 slots


def _parse_bounds(bounds_str):
    nums = re.findall(r'\d+', bounds_str)
    return tuple(int(n) for n in nums) if len(nums) == 4 else None


def dump_elements(serial=None):
    """Return list of interactive elements from UI hierarchy.

    Each element: {label, cx, cy, x1, y1, x2, y2, text, resource_id, clickable}
    Raises RuntimeError if uiautomator2 not installed.
    """
    if not HAS_U2:
        raise RuntimeError(
            "uiautomator2 not installed. Run: pip install uiautomator2 "
            "then: python -m uiautomator2 init"
        )

    d = u2.connect(serial) if serial else u2.connect()
    xml_raw = d.dump_hierarchy()
    xml_str = xml_raw if isinstance(xml_raw, str) else xml_raw.decode()

    root = ET.fromstring(xml_str)
    elements = []
    seen = set()

    for node in root.iter('node'):
        clickable = node.get('clickable') == 'true'
        focusable = node.get('focusable') == 'true'
        if not (clickable or focusable):
            continue

        bounds = _parse_bounds(node.get('bounds', ''))
        if not bounds or bounds in seen:
            continue
        x1, y1, x2, y2 = bounds
        if x2 <= x1 or y2 <= y1:
            continue
        seen.add(bounds)

        if len(elements) >= len(LABELS):
            break

        elements.append({
            'label':       LABELS[len(elements)],
            'cx':          (x1 + x2) // 2,
            'cy':          (y1 + y2) // 2,
            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
            'text':        node.get('text', ''),
            'resource_id': node.get('resource-id', ''),
            'clickable':   clickable,
        })

    return elements
