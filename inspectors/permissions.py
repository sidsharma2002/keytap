from adb import adb


def fetch(pkg):
    """Returns [(permission, 'GRANTED'|'DENIED')]"""
    items = []
    try:
        out = adb("shell", "dumpsys", "package", pkg).stdout.decode()
        for line in out.splitlines():
            s = line.strip()
            if ": granted=" in s:
                perm, rest = s.split(": granted=", 1)
                granted = rest.split(",")[0].strip() == "true"
                items.append((perm.strip(), "GRANTED" if granted else "DENIED"))
    except Exception as e:
        items = [("error", str(e))]
    return items
