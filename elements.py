import re
import xml.etree.ElementTree as ET

try:
    import uiautomator2 as u2
    HAS_U2 = True
except ImportError:
    HAS_U2 = False

# reserved: e=exit, b=back, h=home, r=recents, w/s=scroll
_CHARS = [c for c in 'abcdefghijklmnopqrstuvwxyz' if c not in 'behrsw']  # 20 chars
LABEL_CHARS = frozenset(_CHARS)
LABELS = [a + b for a in _CHARS for b in _CHARS]  # 400 two-char labels


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


# ── Hierarchy tree dump ───────────────────────────────────────────────────────

def _node_to_dict(el):
    cls = el.get('class', '')
    short_cls = cls.rsplit('.', 1)[-1] if '.' in cls else cls

    rid = el.get('resource-id', '')
    if ':id/' in rid:
        rid = rid.split(':id/', 1)[1]

    bounds = _parse_bounds(el.get('bounds', ''))
    cx = cy = 0
    if bounds:
        x1, y1, x2, y2 = bounds
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

    return {
        'class_name':   short_cls,
        'text':         el.get('text', '').strip(),
        'resource_id':  rid,
        'content_desc': el.get('content-desc', '').strip(),
        'bounds':       bounds,
        'cx': cx, 'cy': cy,
        'clickable':    el.get('clickable') == 'true',
        'children':     [_node_to_dict(child) for child in el],
    }


def _collect_flat(node, out):
    out.append(node)
    for child in node['children']:
        _collect_flat(child, out)


def dump_hierarchy_tree(serial=None):
    """Return (root_dict, flat_node_list). Uses u2 if available, else uiautomator dump."""
    if HAS_U2:
        d = u2.connect(serial) if serial else u2.connect()
        xml_raw = d.dump_hierarchy()
        xml_str = xml_raw if isinstance(xml_raw, str) else xml_raw.decode()
    else:
        import subprocess
        cmd = ['adb']
        if serial:
            cmd += ['-s', serial]
        cmd += ['shell', 'uiautomator', 'dump', '/dev/stdout']
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        xml_str = result.stdout.decode(errors='replace').strip()

    root_el = ET.fromstring(xml_str)
    if root_el.tag == 'hierarchy':
        children = list(root_el)
        if len(children) == 1:
            root_node = _node_to_dict(children[0])
        else:
            root_node = {
                'class_name': 'root', 'text': '', 'resource_id': '',
                'content_desc': '', 'bounds': None, 'cx': 0, 'cy': 0,
                'clickable': False,
                'children': [_node_to_dict(c) for c in children],
            }
    else:
        root_node = _node_to_dict(root_el)

    flat = []
    _collect_flat(root_node, flat)
    return root_node, flat
