# keytap — Roadmap

## Control

- **Long press** — hold `Space` triggers long-press (`adb shell input swipe x y x y 800ms`)
- **Pinch / zoom** — `+`/`-` keys fire two-finger swipe pair via ADB
- **Pull to refresh** — single key, swipes down from screen center
- **Macro recorder** — record tap/swipe sequence, replay with one key; great for login/onboarding flows
- **Screen search (OCR)** — Cmd+F scans current frame with pytesseract, jump cursor to matched text

## Inspect

- **Logcat viewer** — filtered logcat stream inside palette, type to grep by tag/package
- **Crash detector** — watches logcat for ANR/fatal, auto-pops crash summary + stacktrace in palette
- **SQLite browser** — list app DBs, show tables, run SELECT queries (extends shared prefs pattern)
- **Storage inspector** — browse `/data/data/<pkg>/files/` tree via `adb run-as`
- **App info** — quick view of versionName, versionCode, targetSDK, install date, APK size

## Debug

- **Network proxy toggle** — set/unset `adb shell settings put global http_proxy` for Charles/Proxyman
- **Dev options toggles** — overdraw, layout bounds, animation scale → all via `adb shell settings`
- **ADB shell REPL** — type arbitrary shell commands from palette, output shown inline
- **Feature flag editor** — filter SharedPrefs to boolean-only keys, toggle directly from list

## Productivity

- **Text snippet library** — saved reusable strings (test emails, phone numbers, OTPs), searchable, paste to device
- **Multi-device broadcast** — send same tap/input to all connected devices simultaneously
- **Screenshot to clipboard** — `adb exec-out screencap -p` → macOS clipboard, single key
