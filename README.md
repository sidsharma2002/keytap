# keytap

Control your Android device entirely from your keyboard. No mouse, no Android Studio, no background daemons. Pure Python + ADB.

Built for mobile developers and QA engineers who want fast, scriptable device control without leaving the terminal.

---

## Features

### Control
- **Grid navigation** — screen divided into a cell grid, move with arrow keys, tap with Space
- **Fast jump** — Shift+Arrow jumps 5 cells at once
- **Element tap** — press `e` to overlay labeled bounding boxes on every interactive element, type a 2-char label to tap it precisely

### Inspect
- **Layout Inspector** — live bounding box overlay on real UI elements with class, resource ID, and text labels
- **View Hierarchy Explorer** — browse the full UI tree from the command palette, scroll through nodes, and see live bound highlights on screen as you navigate

### Debug
- **Memory Watchdog** — real-time GC pressure gauge and process death risk indicator for the foreground app, updated every 2s
- **Shared Prefs Viewer** — browse and search all shared preference keys for any installed package
- **Remote Config Viewer** — inspect remote config keys and values live, with search
- **Permissions Manager** — view and toggle runtime permissions for any package

### Command Palette (double Shift)
- Fuzzy search all installed packages
- **App actions**: launch, force stop, clear data, uninstall
- **Deep link launcher** — fire any `scheme://path` directly to the device
- **Clipboard paste** — paste Mac clipboard text into any device input field
- **Screenshot** — save a timestamped screenshot to your desktop

---

## Install

### One-liner (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/sidsharma2002/keytap/development/bootstrap.sh | bash
```

Clones the repo to `~/keytap`, installs Python dependencies, and registers the `keytap` command in your shell. Open a new terminal and run:

```bash
keytap
```

### Manual

```bash
git clone https://github.com/sidsharma2002/keytap.git
cd keytap
bash install.sh
```

### System requirements

- **adb** — `brew install android-platform-tools`
- **ffmpeg** — `brew install ffmpeg` (optional, used by screenrecord fallback)
- Python 3.10+

---

## Usage

Connect a device, then:

```bash
keytap                          # default (scrcpy backend, best fps)
keytap --serial emulator-5554   # target a specific device
keytap --height 1000            # taller mirror window
keytap --backend screenrecord   # fallback capture
```

---

## Keybindings

| Key | Action |
|---|---|
| Arrow keys | Move cursor |
| Shift+Arrow | Jump 5 cells |
| Space | Tap at cursor |
| `e` | Toggle element overlay |
| `b` | Back |
| `h` | Home |
| `r` | Recent apps |
| `w` / `s` | Scroll up / down |
| Shift+Shift | Open command palette |
| Esc | Exit current mode |

---

## License

MIT
