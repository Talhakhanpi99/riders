# VoiceRide Architecture

## System Overview

VoiceRide is a local Android WebView application. Flask serves the UI and JSON APIs from Python, while voice actions execute device actions through a narrow Android bridge.

On Android, `main.py` starts the local Flask server and `platform_bridge.py` attaches an Android WebView to `http://127.0.0.1:5000`. On desktop, the same entry point runs as a normal Flask app.

The Python source is intentionally compact:

- `main.py`: process entry point.
- `app.py`: Flask, routes, settings, SQLite, repositories, and dependency wiring.
- `voice_core.py`: wake word, parser, conversation context, dispatcher, permissions, and feature actions.
- `platform_bridge.py`: Android WebView/native bridge with desktop-safe fallbacks.

## Pipeline

1. Wake word detection accepts the default wake word `Phone` and can later load custom wake models.
2. Speech recognition starts through the Android bridge.
3. Intent detection parses multilingual natural speech offline.
4. Conversation context tracks recent turns and pending clarifications.
5. Action dispatcher routes the intent to the matching feature action.
6. Android native bridge performs platform operations with defensive fallbacks.
7. Voice output speaks a concise natural response.

## Clean Boundaries

- Flask routes stay thin and call `AssistantService`.
- AI parsing never calls Android APIs.
- Persistence is accessed through repository classes in `app.py`.
- Android APIs are wrapped by `AndroidNativeBridge` in `platform_bridge.py`.

## Offline AI

The first parser uses deterministic multilingual intent scoring and fuzzy matching. This is intentionally local, fast, and replaceable. Future plugin points can add:

- on-device speech models,
- vector semantic parsing,
- user-trained wake words,
- cloud LLM enhancement when cloud mode is enabled,
- custom command packs.

## Data

SQLite stores:

- settings,
- voice history,
- recent commands,
- frequently contacted people.

## Android Limitations

Modern Android restricts direct toggling of WiFi, hotspot, Bluetooth, and call control. VoiceRide keeps those actions behind service methods so implementations can use supported intents, accessibility services, device-owner policies, or user-confirmable screens depending on Play Store policy and target Android version.
