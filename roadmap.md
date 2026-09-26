# keytap - Product Roadmap

## Navigation Speed

- `Shift+arrow` — jump 5 cells (fast traverse)
- `Option+arrow` — jump to edge (first/last row or col in that direction)
- `g` key + row/col number — vim-style goto (e.g. `g 5 3`)

## Tap Interactions

- Long press: hold `Space` → `adb shell input swipe x y x y 800` (same coords, ~800ms duration)
- Double tap: `d` key
- Swipe from cursor: `s + arrow` — already in label mode controls, needs wiring to cursor mode
- Text input: `t` key → prompt for string → `adb shell input text "..."`

## Device Shortcuts

- `b` → back button
- `h` → home button
- `r` → recent apps
- Most common ops in mobile testing flows

## Macro / History

- `m` → mark current cell (save bookmark, numbered 1-9)
- `1`-`9` → jump cursor to saved bookmark
- `u` → jump cursor back to last-tapped cell (single-step undo)

## Visual Polish

- Ghost dot at last-tapped cell position, fades after 2s — visual confirmation of tap landing
- Show cell address in status bar (already partial, improve to `R3 C7` format)
- `+` / `-` keys → adjust grid density live (cols/rows ±2 per press)

## Overlay UX

- `o` key → toggle grid lines on/off (cursor persists, lines disappear for unobstructed view)
- Always-on-top window (macOS: `NSFloatingWindowLevel` via osascript)
- Global window opacity control (useful when keytap overlaid on figma/jira for reference)

## Command Palette (Spotlight / double-shift style)

Trigger: `/` key. Inline search bar replaces status bar. Results render above it. Esc closes.

### Static action registry (low effort, high value)
Pre-defined name → ADB command. Fuzzy match on name.

| Search term | ADB command |
|---|---|
| "back" | `input keyevent 4` |
| "home" | `input keyevent 3` |
| "lock screen" | `input keyevent 26` |
| "rotate landscape/portrait" | `content insert ...` |
| "wifi settings" | `am start -a android.settings.WIFI_SETTINGS` |
| "app settings" | `am start -a android.settings.APPLICATION_DETAILS_SETTINGS -d package:<pkg>` |
| "input text" | prompt → `input text "..."` |
| "screenshot" | `exec-out screencap -p` → save to desktop |
| "volume up/down" | `input keyevent 24/25` |
| "wake device" | `input keyevent 224` |

### Dynamic app list (moderate effort, very high value)
- Fetch via `adb shell pm list packages -3` at startup (background thread, ~1-2s)
- Cache in memory, refresh with hotkey
- Fuzzy search on package name (skip human labels — too complex for MVP)
- Sub-menu on selection: Launch / Force Stop / Clear Data / Uninstall

| Action | ADB command |
|---|---|
| Launch | `monkey -p <pkg> -c android.intent.category.LAUNCHER 1` |
| Force stop | `am force-stop <pkg>` |
| Clear data | `pm clear <pkg>` |
| Uninstall | `pm uninstall <pkg>` |

### Intent launcher
- Common intents in static registry (settings screens, dialer, browser)
- Raw passthrough: user types `am start ...` → execute directly

### UI design
- Option A: tkinter Toplevel popup (easy, two windows)
- Option B: inline overlay at bottom of mirror window (recommended — one window, polished)
- Arrow keys navigate results, Enter executes, Esc closes

### Not in MVP
- Human-readable app labels (need `aapt` or APK parsing — high effort, medium value)
- Custom intent construction UI
- Multi-step action chains

---

## Developer Utilities

### Tier 1 — Zero/low effort, daily use

**Current activity in status bar**
- Always-on: `adb shell dumpsys activity | grep mResumedActivity` polled every ~2s
- Show `com.example/.LoginActivity` in status bar
- Near-free; constantly useful for navigation + debugging

**Mac → device clipboard paste**
- `t` key reads Mac clipboard via `pbpaste`, pipes to `adb shell input text "..."`
- Eliminates typing emails/tokens/URLs on device

**Screenshot to desktop**
- Single key saves `~/Desktop/keytap_YYYYMMDD_HHMMSS.png`
- `adb exec-out screencap -p` → timestamped file
- Already half-wired in palette; promote to dedicated hotkey

**Notification shade toggle**
- `n` key → `adb shell input swipe 500 0 500 1000`
- Open/close notification drawer without touching device

### Tier 2 — Moderate effort, high developer value

**Logcat tail overlay**
- Toggleable pane (bottom 20% of window) streaming `adb logcat -v brief`
- Filter by foreground package automatically
- Eliminates terminal switching during debugging sessions

**Deep link / intent launcher**
- Palette: type `deep myapp://reset` → `adb shell am start -a android.intent.action.VIEW -d "myapp://reset"`
- Common for feature testing and QA flows

**Permission manager**
- Palette → pick package → list DANGEROUS permissions → grant/revoke inline
- `adb shell pm grant/revoke <pkg> <permission>`
- Common in onboarding flow testing

### Tier 3 — High effort, niche but powerful

**ADB WiFi toggle**
- `adb tcpip 5555` + `adb connect <device-ip>` → wireless debugging
- Auto-detect device IP from `adb shell ip route`

**Tap recorder → adb script**
- Record sequence of taps/swipes → export as shell script with `sleep` between steps
- Lightweight regression macro without uiautomator2 fragility

**Font scale / display density toggle**
- `adb shell settings put system font_scale 1.3` quick toggle
- Test accessibility without digging into device settings

---

## uiautomator2 (Element-Aware Mode)

Uses the `uiautomator2` Python library which runs a persistent agent on device. UI hierarchy dump is ~50-200ms vs 500ms+ for `adb shell uiautomator dump`. Enables element-driven tapping instead of coordinate-driven.

### Element overlay (highest value)
- `e` key → dump hierarchy, parse `bounds="[x1,y1][x2,y2]"` for all interactive elements
- Draw colored bounding boxes + short labels (`a`, `b`, `c`... or `1`, `2`, `3`) on the mirror
- Press label → tap that element's center coords
- Replaces grid for test automation use cases; grid stays for exploratory use
- Two modes: **grid mode** (current, arbitrary tap anywhere) + **element mode** (real UI targets only)

### Element search in palette
- Second mode in existing palette: search visible elements by text or resource-id
- Type "login" → finds `Button[text=Login]` → tap
- No coordinate guessing; survives layout shifts

### Context-aware status bar
- On cursor move, check which element is at those device coords from cached hierarchy
- Show `button: "Sign In" (clickable)` in status bar instead of just cell address
- Near-free once hierarchy is cached

### Smart text input
- When `Space` pressed: check if cursor is on `EditText` via hierarchy
- If yes: auto-prompt for string, use `d.send_keys()` instead of raw `input text`
- If no: normal tap

### Smart scroll
- Current: raw swipe at cursor position
- Better: find scrollable container at cursor coords from hierarchy → `d(scrollable=True).scroll()`
- More reliable, handles nested scrollable containers

### Wait / assert
- `wait <text>` → block until element with that text appears on screen
- Useful for semi-automated test flows: tap → wait for next screen → tap

### Record + replay
- Record taps as element-based actions (`text=`, `resource-id=`) not raw coordinates
- Replay survives layout changes and different screen sizes
- Lightweight macro system without fragile coordinate hardcoding

---

## Priority Order

1. `Shift+arrow` fast jump — one-liner, massive nav speed improvement ✅
2. `b` / `h` / `r` device shortcuts — common in mobile testing ✅
3. `w` / `s` scroll — swipe up/down at cursor position ✅
4. Command palette — double-Shift, fuzzy search over packages + actions ✅
5. u2 element overlay — draw real element bounds on mirror, tap by label
6. u2 element search in palette — search visible UI elements by text
7. u2 context status bar — show element info on cursor hover
8. Current activity in status bar — always-on, near-free developer info
9. Mac → device clipboard paste — removes biggest text input friction
10. Logcat tail overlay — eliminates terminal switching during debugging
11. Ghost dot on last tap — immediate visual feedback, eliminates guessing
12. Notification shade toggle (`n` key)
13. Screenshot to desktop (dedicated hotkey)
14. Deep link / intent launcher (in palette)
15. Long press (`Space` hold)
16. Smart text input (`t` key / u2 EditText detection)
17. Smart scroll (u2 scrollable container detection)
18. Permission manager (in palette)
19. Bookmarks + undo history
20. Grid density toggle (`+` / `-`)
21. Grid visibility toggle (`o`)
22. Always-on-top window
23. Font scale / density quick toggle
24. ADB WiFi toggle
25. Tap recorder → adb script export
26. u2 wait/assert
27. u2 record + replay
