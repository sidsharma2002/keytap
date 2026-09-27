import subprocess
import sys
import time as _time
import xml.etree.ElementTree as ET

try:
    import uiautomator2 as u2
    HAS_U2 = True
except ImportError:
    HAS_U2 = False

from utils import parse_bounds


def _node_to_dict(el):
    cls = el.get('class', '')
    short_cls = cls.rsplit('.', 1)[-1] if '.' in cls else cls

    rid = el.get('resource-id', '')
    if ':id/' in rid:
        rid = rid.split(':id/', 1)[1]

    bounds = parse_bounds(el.get('bounds', ''))
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


def dump_hierarchy_tree(serial=None, u2_dev=None):
    """Return (root_dict, flat_node_list). Uses u2 if available, else adb uiautomator dump."""
    _t0 = _time.time()
    print(f"[DEBUG][hierarchy] dump_hierarchy_tree start, HAS_U2={HAS_U2}, serial={serial!r}, preconnected={u2_dev is not None}", file=sys.stderr, flush=True)
    if HAS_U2:
        if u2_dev is None:
            print(f"[DEBUG][hierarchy] u2.connect() ...", file=sys.stderr, flush=True)
            u2_dev = u2.connect(serial) if serial else u2.connect()
            print(f"[DEBUG][hierarchy] u2.connect() done in {_time.time()-_t0:.2f}s", file=sys.stderr, flush=True)
        else:
            print(f"[DEBUG][hierarchy] using pre-connected u2 device", file=sys.stderr, flush=True)
        _t1 = _time.time()
        xml_raw = u2_dev.dump_hierarchy()
        print(f"[DEBUG][hierarchy] dump_hierarchy() done in {_time.time()-_t1:.2f}s, total {_time.time()-_t0:.2f}s", file=sys.stderr, flush=True)
        xml_str = xml_raw if isinstance(xml_raw, str) else xml_raw.decode()
    else:
        cmd = ['adb']
        if serial:
            cmd += ['-s', serial]
        cmd += ['shell', 'uiautomator', 'dump', '/dev/stdout']
        print(f"[DEBUG][hierarchy] adb uiautomator dump ...", file=sys.stderr, flush=True)
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        print(f"[DEBUG][hierarchy] adb dump done in {_time.time()-_t0:.2f}s, stdout_len={len(result.stdout)}", file=sys.stderr, flush=True)
        xml_str = result.stdout.decode(errors='replace').strip()

    _tp = _time.time()
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
    print(f"[DEBUG][hierarchy] parse+flatten done in {_time.time()-_tp:.2f}s, {len(flat)} nodes, total {_time.time()-_t0:.2f}s", file=sys.stderr, flush=True)
    return root_node, flat
