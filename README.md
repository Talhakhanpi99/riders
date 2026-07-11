# VoiceRide

VoiceRide is an offline-first AI voice assistant for motorcycle riders and drivers. It is built as an Android WebView application with a Python Flask backend, SQLite persistence, and a compact intent pipeline that understands natural mixed-language commands.

The first target use case is Pakistan-style bike riding: the rider says a configurable wake word such as `phone`, gets an obvious wake signal, then speaks a short command in English, Roman Urdu, Urdu, or mixed local wording. The assistant focuses on quick phone control without Bluetooth dependency: calls, SMS, torch, brightness, apps, messages, and safe clarifications when contact names are ambiguous.

## Goals

- Keep core assistant behavior offline by default.
- Start listening quickly after the wake word.
- Signal wake state with screen flash, torch blink, vibration, or sound.
- Avoid wrong calls/messages through contact clarification and optional confirmation.
- Let users configure wake word, wake timeout, wake feedback, and voice profile state.
- Keep user data on-device unless cloud mode is explicitly enabled.
- Keep the Python source easy to share and debug by using only a few main scripts.
- Provide Android-ready permissions, Buildozer configuration, CI, docs, and tests.

## Architecture

The assistant pipeline is:

`Wake Word -> Speech Recognition -> Intent Detection -> Context Manager -> Action Dispatcher -> Android Native Bridge -> Voice Response`

Important files:

- `main.py`: Android/desktop entry point.
- `app.py`: Flask app factory, routes, settings, SQLite repositories, and logging.
- `voice_core.py`: wake word, intent parser, context, dispatcher, permissions, and feature actions.
- `platform_bridge.py`: Android WebView launcher and native API bridge.
- `templates/` and `static/`: WebView UI.
- `buildozer.spec` and `.github/workflows/android.yml`: APK build configuration.
- `docs/`: architecture and API documentation.

## Local Development

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Open `http://127.0.0.1:5000`.

## Android Build

Buildozer is configured in `buildozer.spec`. Run Android builds from WSL/Linux, not from Windows PowerShell.

```bash
cd /mnt/d/PromptPacks/talhanew
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -r requirements.txt
buildozer android debug
```

If `source venv/bin/activate` works but `python` or `buildozer` is still missing, the venv was likely copied from another folder. Recreate it from WSL:

```bash
deactivate 2>/dev/null || true
mv venv venv-broken
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -r requirements.txt
```

Production signing should use a protected keystore configured in CI secrets before Play Store release.

## Testing

```bash
pytest -q
ruff check .
```

## Privacy

VoiceRide defaults to offline mode. Commands, logs, settings, wake words, and history are stored in local SQLite. Cloud mode is a settings flag and should only be wired to external AI providers after explicit user consent and visible privacy controls.
