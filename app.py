"""Flask application, database, settings, routes, and dependency wiring."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from platform_bridge import AndroidNativeBridge
from voice_core import AssistantService, PermissionManager

BASE_DIR = Path(__file__).resolve().parent
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AppSettings:
    app_name: str = "VoiceRide"
    environment: str = os.getenv("VOICERIDE_ENV", "production")
    database_path: Path = Path(os.getenv("VOICERIDE_DB", BASE_DIR / "database" / "voiceride.db"))
    log_dir: Path = Path(os.getenv("VOICERIDE_LOG_DIR", BASE_DIR / "logs"))
    default_wake_word: str = os.getenv("VOICERIDE_WAKE_WORD", "phone")
    log_level: str = os.getenv("VOICERIDE_LOG_LEVEL", "INFO")
    max_log_bytes: int = int(os.getenv("VOICERIDE_MAX_LOG_BYTES", "1048576"))
    backup_log_count: int = int(os.getenv("VOICERIDE_BACKUP_LOG_COUNT", "5"))


@dataclass(frozen=True)
class UserSettings:
    language: str = "mixed"
    voice: str = "default"
    speech_speed: float = 1.0
    wake_word: str = "phone"
    wake_timeout_seconds: int = 5
    wake_feedback_mode: str = "screen_flash"
    confirm_before_call: bool = False
    confirm_before_message: bool = True
    voice_profile_enabled: bool = True
    voice_training_complete: bool = False
    offline_mode: bool = True
    cloud_mode: bool = False
    theme: str = "dark"


settings = AppSettings()


def configure_logging() -> logging.Logger:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("voiceride")
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(module)s:%(lineno)d | %(message)s")
    file_handler = RotatingFileHandler(
        settings.log_dir / "voiceride.log",
        maxBytes=settings.max_log_bytes,
        backupCount=settings.backup_log_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


class Database:
    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path or settings.database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS voice_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_text TEXT NOT NULL,
                    intent_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    response TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS recent_commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT NOT NULL,
                    response TEXT NOT NULL,
                    execution_ms INTEGER NOT NULL,
                    success INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS frequent_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    usage_count INTEGER DEFAULT 1,
                    last_used_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(name, phone_number)
                );
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )


class SettingsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_all(self) -> UserSettings:
        values = UserSettings().__dict__.copy()
        with self.database.connect() as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        for row in rows:
            values[row["key"]] = json.loads(row["value"])
        return UserSettings(**values)

    def set_value(self, key: str, value: Any) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, json.dumps(value)),
            )


class HistoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record_voice_history(self, intent: Any, result: Any) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO voice_history (raw_text, intent_type, confidence, response)
                VALUES (?, ?, ?, ?)
                """,
                (intent.raw_text, intent.intent_type.value, intent.confidence, result.spoken_response),
            )

    def record_command(self, command: str, result: Any, execution_ms: int) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO recent_commands (command, response, execution_ms, success)
                VALUES (?, ?, ?, ?)
                """,
                (command, result.spoken_response, execution_ms, int(result.success)),
            )

    def recent_commands(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT command, response, execution_ms, success, created_at
                FROM recent_commands
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


class ContactsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def increment_usage(self, name: str, phone_number: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO frequent_contacts (name, phone_number, usage_count)
                VALUES (?, ?, 1)
                ON CONFLICT(name, phone_number) DO UPDATE SET
                    usage_count = usage_count + 1,
                    last_used_at = CURRENT_TIMESTAMP
                """,
                (name, phone_number),
            )


class SettingsService:
    ALLOWED_KEYS = set(UserSettings().__dict__.keys())
    BOOLEAN_KEYS = {
        "confirm_before_call",
        "confirm_before_message",
        "voice_profile_enabled",
        "voice_training_complete",
        "offline_mode",
        "cloud_mode",
    }
    WAKE_FEEDBACK_MODES = {"screen_flash", "torch_blink", "vibrate", "sound"}

    def __init__(self, repository: SettingsRepository) -> None:
        self.repository = repository

    def get(self) -> UserSettings:
        values = self.repository.get_all().__dict__.copy()
        defaults = UserSettings().__dict__
        normalized = {}
        for key, value in values.items():
            try:
                normalized[key] = self._coerce_value(key, value)
            except (TypeError, ValueError):
                normalized[key] = defaults[key]
        return UserSettings(**normalized)

    def update(self, values: dict[str, Any]) -> UserSettings:
        for key, value in values.items():
            if key in self.ALLOWED_KEYS:
                self.repository.set_value(key, self._coerce_value(key, value))
        return self.get()

    def _coerce_value(self, key: str, value: Any) -> Any:
        if key in self.BOOLEAN_KEYS:
            return value.strip().lower() in {"1", "true", "yes", "on"} if isinstance(value, str) else bool(value)
        if key == "speech_speed":
            return max(0.7, min(1.3, float(value)))
        if key == "wake_timeout_seconds":
            return max(2, min(15, int(value)))
        if key == "wake_feedback_mode":
            return value if value in self.WAKE_FEEDBACK_MODES else "screen_flash"
        if key == "wake_word":
            return str(value).strip() or "phone"
        return value


def create_app() -> Flask:
    flask_app = Flask(__name__, template_folder="templates", static_folder="static")
    logger = configure_logging()
    database = Database()
    settings_repository = SettingsRepository(database)
    history_repository = HistoryRepository(database)
    contacts_repository = ContactsRepository(database)
    settings_service = SettingsService(settings_repository)
    bridge = AndroidNativeBridge(logger)
    assistant = AssistantService(
        bridge=bridge,
        history=history_repository,
        contacts_repository=contacts_repository,
        settings_repository=settings_repository,
        settings_service=settings_service,
        default_wake_word=settings.default_wake_word,
        logger=logger,
    )
    permission_manager = PermissionManager(logger)
    register_routes(flask_app, assistant, settings_service, history_repository, permission_manager, bridge)
    return flask_app


def register_routes(
    flask_app: Flask,
    assistant: AssistantService,
    settings_service: SettingsService,
    history_repository: HistoryRepository,
    permission_manager: PermissionManager,
    bridge: AndroidNativeBridge,
) -> None:
    @flask_app.get("/")
    def index() -> str:
        return render_template("index.html")

    @flask_app.get("/settings")
    def settings_page() -> str:
        return render_template("settings.html")

    @flask_app.get("/logs")
    def logs_page() -> str:
        return render_template("logs.html")

    @flask_app.get("/developer")
    def developer_page() -> str:
        return render_template("developer.html")

    @flask_app.get("/api/status")
    def status() -> Any:
        return jsonify(
            {
                "app": "VoiceRide",
                "android_available": bridge.android_available,
                "settings": asdict(settings_service.get()),
            }
        )

    @flask_app.post("/api/command")
    def command() -> Any:
        payload = request.get_json(silent=True) or {}
        return jsonify(
            assistant.handle_text(
                str(payload.get("text", "")),
                require_wake_word=bool(payload.get("require_wake_word", False)),
            )
        )

    @flask_app.post("/api/listen/start")
    def start_listening() -> Any:
        payload = request.get_json(silent=True) or {}
        timeout = max(2, min(15, int(payload.get("timeout_seconds", settings_service.get().wake_timeout_seconds))))
        return jsonify(bridge.start_listening(timeout))

    @flask_app.get("/api/listen/result")
    def listening_result() -> Any:
        event = bridge.consume_speech_result()
        if event["status"] != "result":
            return jsonify(event)
        transcript = event.get("transcript", "")
        user_settings = settings_service.get()
        woke, remainder = assistant.wake_detector.remove_wake_word(transcript, user_settings.wake_word)
        if woke and not remainder:
            assistant.arm_follow_up(user_settings.wake_timeout_seconds)
            bridge.signal_wake(user_settings.wake_feedback_mode)
            bridge.speak("Yes, I am here.", user_settings.speech_speed)
            return jsonify({"status": "wake_detected", "transcript": transcript, "response": "Yes, I am here."})
        result = assistant.handle_text(transcript, require_wake_word=not assistant.consume_follow_up())
        result.update({"status": "completed", "transcript": transcript})
        return jsonify(result)
    @flask_app.post("/api/offline/transcript")
    def offline_transcript() -> Any:
        """Receive a final local Vosk transcript from the foreground service."""
        payload = request.get_json(silent=True) or {}
        transcript = str(payload.get("transcript", "")).strip()
        if not transcript:
            return jsonify({"status": "ignored", "message": "No transcript received."}), 400
        user_settings = settings_service.get()
        woke, remainder = assistant.wake_detector.remove_wake_word(transcript, user_settings.wake_word)
        if woke and not remainder:
            assistant.arm_follow_up(user_settings.wake_timeout_seconds)
            bridge.signal_wake(user_settings.wake_feedback_mode)
            bridge.speak("Yes, I am here.", user_settings.speech_speed)
            return jsonify({"status": "wake_detected", "transcript": transcript, "response": "Yes, I am here."})
        result = assistant.handle_text(transcript, require_wake_word=not assistant.consume_follow_up())
        result.update({"status": "completed", "transcript": transcript})
        return jsonify(result)

    @flask_app.post("/api/offline-listener/start")
    def start_offline_listener() -> Any:
        return jsonify(bridge.start_offline_listener())

    @flask_app.post("/api/offline-listener/stop")
    def stop_offline_listener() -> Any:
        return jsonify(bridge.stop_offline_listener())
    @flask_app.get("/api/settings")
    def get_settings() -> Any:
        return jsonify(asdict(settings_service.get()))

    @flask_app.put("/api/settings")
    def update_settings() -> Any:
        payload = request.get_json(silent=True) or {}
        return jsonify(asdict(settings_service.update(payload)))

    @flask_app.get("/api/commands/recent")
    def recent_commands() -> Any:
        return jsonify(history_repository.recent_commands())

    @flask_app.get("/api/permissions")
    def permissions() -> Any:
        return jsonify(
            [
                {
                    "key": descriptor.key,
                    "android_name": descriptor.android_name,
                    "rationale": descriptor.rationale,
                    "granted": permission_manager.has_permission(descriptor.key),
                }
                for descriptor in permission_manager.PERMISSIONS.values()
            ]
        )

    @flask_app.post("/api/permissions/request")
    def request_runtime_permissions() -> Any:
        """Ask Android for the permissions used by the visible app controls."""
        payload = request.get_json(silent=True) or {}
        requested_keys = payload.get("keys", ["microphone", "camera"])
        if not isinstance(requested_keys, list):
            requested_keys = ["microphone", "camera"]
        permissions = [
            f"android.permission.{permission_manager.PERMISSIONS[key].android_name}"
            for key in requested_keys
            if key in permission_manager.PERMISSIONS
        ]
        grants = bridge.request_permissions(permissions)
        return jsonify(
            {
                "granted": {
                    key: grants.get(f"android.permission.{descriptor.android_name}", False)
                    for key, descriptor in permission_manager.PERMISSIONS.items()
                    if key in requested_keys
                }
            }
        )
    @flask_app.post("/api/diagnostics/speech")
    def speech_diagnostic() -> Any:
        return jsonify(bridge.speech_diagnostic())

    @flask_app.get("/api/diagnostics/logs")
    def diagnostic_logs() -> Any:
        try:
            lines = (settings.log_dir / "voiceride.log").read_text(encoding="utf-8", errors="replace").splitlines()[-120:]
        except Exception as exc:
            lines = [f"Could not read diagnostics log: {exc}"]
        return jsonify({"lines": lines})

    @flask_app.get("/api/diagnostics")
    def diagnostics() -> Any:
        permission_state = {
            key: permission_manager.has_permission(key)
            for key in permission_manager.PERMISSIONS
        }
        return jsonify({
            "app": "VoiceRide",
            "android_available": bridge.android_available,
            "permissions": permission_state,
            "settings": asdict(settings_service.get()),
            "recent_commands": history_repository.recent_commands(),
        })


