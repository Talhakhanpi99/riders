"""Offline parsing and safe action orchestration for VoiceRide."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IntentType(str, Enum):
    UNKNOWN = "unknown"
    HELP = "help"
    CONFIRM_ACTION = "confirm_action"
    CANCEL_ACTION = "cancel_action"
    CALL_CONTACT = "call_contact"
    SEND_SMS = "send_sms"
    FLASHLIGHT_ON = "flashlight_on"
    FLASHLIGHT_OFF = "flashlight_off"
    BRIGHTNESS_SET = "brightness_set"
    BRIGHTNESS_UP = "brightness_up"
    BRIGHTNESS_DOWN = "brightness_down"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    VOLUME_MUTE = "volume_mute"
    WIFI_ON = "wifi_on"
    WIFI_OFF = "wifi_off"
    BLUETOOTH_ON = "bluetooth_on"
    BLUETOOTH_OFF = "bluetooth_off"
    HOTSPOT_ON = "hotspot_on"
    HOTSPOT_OFF = "hotspot_off"
    OPEN_CAMERA = "open_camera"
    CLOSE_APP = "close_app"
    BATTERY_STATUS = "battery_status"
    READ_LATEST_SMS = "read_latest_sms"
    READ_NOTIFICATIONS = "read_notifications"


@dataclass(frozen=True)
class ParsedIntent:
    intent_type: IntentType
    raw_text: str
    entities: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass(frozen=True)
class ActionResult:
    success: bool
    spoken_response: str
    intent_type: IntentType
    needs_confirmation: bool = False


@dataclass(frozen=True)
class PermissionDescriptor:
    key: str
    android_name: str
    rationale: str


class PermissionManager:
    """Desktop-safe Android permission status checker."""
    PERMISSIONS = {
        "microphone": PermissionDescriptor("microphone", "RECORD_AUDIO", "Needed to hear voice commands."),
        "notifications": PermissionDescriptor("notifications", "POST_NOTIFICATIONS", "Needed for notification alerts."),
        "camera": PermissionDescriptor("camera", "CAMERA", "Needed to control the torch and camera."),
        "location": PermissionDescriptor("location", "ACCESS_FINE_LOCATION", "Needed for location features."),
        "contacts": PermissionDescriptor("contacts", "READ_CONTACTS", "Needed to find contacts by name."),
        "phone": PermissionDescriptor("phone", "CALL_PHONE", "Needed to place a call."),
        "send_sms": PermissionDescriptor("send_sms", "SEND_SMS", "Needed to send a text message."),
        "read_sms": PermissionDescriptor("read_sms", "READ_SMS", "Needed to read the latest text message."),
    }

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("voiceride")

    def has_permission(self, key: str) -> bool:
        descriptor = self.PERMISSIONS.get(key)
        if not descriptor:
            return False
        try:
            from android.permissions import check_permission  # type: ignore
            return bool(check_permission(f"android.permission.{descriptor.android_name}"))
        except Exception:
            return True


class WakeWordDetector:
    def __init__(self, default_wake_word: str = "phone") -> None:
        self.default_wake_word = default_wake_word

    def remove_wake_word(self, text: str, wake_word: str | None = None) -> tuple[bool, str]:
        word = (wake_word or self.default_wake_word).strip().lower()
        clean = " ".join((text or "").strip().split())
        if not word:
            return True, clean
        match = re.match(rf"^{re.escape(word)}(?:[\s,.:!]+|$)(.*)$", clean, re.I)
        return (True, match.group(1).strip()) if match else (False, clean)


class IntentParser:
    """Deterministic English/Roman-Urdu parser; no network dependency."""
    YES = {"yes", "yes please", "haan", "han", "ha", "theek", "theek hai", "confirm", "ok", "okay"}
    NO = {"no", "nahin", "nahi", "cancel", "ruko", "stop"}

    def parse(self, text: str) -> ParsedIntent:
        raw, value = text or "", self._normalise(text or "")
        if not value:
            return ParsedIntent(IntentType.UNKNOWN, raw)
        if value in self.YES:
            return ParsedIntent(IntentType.CONFIRM_ACTION, raw, confidence=.98)
        if value in self.NO:
            return ParsedIntent(IntentType.CANCEL_ACTION, raw, confidence=.98)
        if any(x in value for x in ("help", "madad", "commands", "kya kar sakte")):
            return ParsedIntent(IntentType.HELP, raw, confidence=.94)
        if "battery" in value:
            return ParsedIntent(IntentType.BATTERY_STATUS, raw, confidence=.97)
        if any(x in value for x in ("latest sms", "last message", "sms parho")):
            return ParsedIntent(IntentType.READ_LATEST_SMS, raw, confidence=.92)
        if "notification" in value:
            return ParsedIntent(IntentType.READ_NOTIFICATIONS, raw, confidence=.92)
        match = re.search(r"(?:brightness|roshni)\s*(?:to|set)?\s*(\d{1,3})\s*(?:%|percent)?", value)
        if match:
            return ParsedIntent(IntentType.BRIGHTNESS_SET, raw, {"percentage": match.group(1)}, .96)
        for phrases, kind in (
            (("brightness up", "increase brightness", "roshni barhao"), IntentType.BRIGHTNESS_UP),
            (("brightness down", "decrease brightness", "roshni kam"), IntentType.BRIGHTNESS_DOWN),
            (("torch on", "open torch", "open the torch", "flashlight on", "light on", "torch kholo"), IntentType.FLASHLIGHT_ON),
            (("torch off", "close torch", "close the light", "flashlight off", "light off", "torch band"), IntentType.FLASHLIGHT_OFF),
            (("volume up", "increase volume", "volume barhao"), IntentType.VOLUME_UP),
            (("volume down", "decrease volume", "volume kam"), IntentType.VOLUME_DOWN),
        ):
            if any(x in value for x in phrases):
                return ParsedIntent(kind, raw, confidence=.93)
        if "mute" in value or "volume band" in value:
            return ParsedIntent(IntentType.VOLUME_MUTE, raw, confidence=.93)
        toggle = self._toggle(value)
        if toggle:
            return ParsedIntent(toggle, raw, confidence=.92)
        if "camera" in value and any(x in value for x in ("open", "start", "kholo")):
            return ParsedIntent(IntentType.OPEN_CAMERA, raw, confidence=.91)
        if any(x in value for x in ("close app", "band app", "close application")):
            return ParsedIntent(IntentType.CLOSE_APP, raw, confidence=.88)
        sms = self._sms(value)
        if sms:
            return ParsedIntent(IntentType.SEND_SMS, raw, sms, .96)
        call = self._call(value)
        if call:
            return ParsedIntent(IntentType.CALL_CONTACT, raw, call, .95)
        return ParsedIntent(IntentType.UNKNOWN, raw, confidence=.15)

    @staticmethod
    def _normalise(value: str) -> str:
        return " ".join(re.sub(r"[^\w\s%+]", " ", value.lower()).split())

    @staticmethod
    def _toggle(value: str) -> IntentType | None:
        for name, on, off in (("wifi", IntentType.WIFI_ON, IntentType.WIFI_OFF), ("wi fi", IntentType.WIFI_ON, IntentType.WIFI_OFF), ("bluetooth", IntentType.BLUETOOTH_ON, IntentType.BLUETOOTH_OFF), ("hotspot", IntentType.HOTSPOT_ON, IntentType.HOTSPOT_OFF)):
            if name in value:
                if any(x in value for x in ("on", "open", "start", "kholo")):
                    return on
                if any(x in value for x in ("off", "close", "band", "stop")):
                    return off
        return None

    @staticmethod
    def _sms(value: str) -> dict[str, str] | None:
        for pattern in (r"(?:send (?:a )?(?:message|sms) to|message|sms)\s+(.+?)\s+(?:that |saying |bolo\s+)(.+)$", r"(.+?)\s+ko\s+bolo\s+(.+)$"):
            match = re.match(pattern, value)
            if match:
                return {"contact_name": match.group(1).strip(), "message": match.group(2).strip()}
        return None

    @staticmethod
    def _call(value: str) -> dict[str, str] | None:
        for pattern in (r"(?:call|dial|phone)\s+(.+)$", r"(.+?)\s+ko\s+(?:call|phone)\s+(?:lagao|karo)$"):
            match = re.match(pattern, value)
            if match:
                name = match.group(1).strip()
                return {"contact_name": name, "phone_number": re.sub(r"[^0-9+]", "", name)}
        return None


class AssistantService:
    def __init__(self, *, bridge: Any, history: Any, contacts_repository: Any, settings_repository: Any, settings_service: Any, default_wake_word: str, logger: logging.Logger | None = None) -> None:
        self.bridge, self.history = bridge, history
        self.contacts_repository, self.settings_repository = contacts_repository, settings_repository
        self.settings_service = settings_service
        self.logger = logger or logging.getLogger("voiceride")
        self.parser, self.wake_detector, self.pending = IntentParser(), WakeWordDetector(default_wake_word), None
        self._follow_up_until = 0.0

    def arm_follow_up(self, timeout_seconds: int) -> None:
        self._follow_up_until = time.monotonic() + max(2, min(15, timeout_seconds))

    def consume_follow_up(self) -> bool:
        active = time.monotonic() <= self._follow_up_until
        self._follow_up_until = 0.0
        return active
    def handle_text(self, text: str, require_wake_word: bool = False) -> dict[str, Any]:
        started, settings = time.monotonic(), self.settings_service.get()
        woke, command = self.wake_detector.remove_wake_word(text, settings.wake_word)
        if require_wake_word and not woke:
            return self._response(ActionResult(False, f"Say {settings.wake_word} first.", IntentType.UNKNOWN), None)
        if woke:
            self.bridge.signal_wake(settings.wake_feedback_mode)
        intent = self.parser.parse(command)
        if intent.intent_type == IntentType.CONFIRM_ACTION:
            result = self._confirm()
        elif intent.intent_type == IntentType.CANCEL_ACTION:
            self.pending, result = None, ActionResult(True, "Cancelled.", intent.intent_type)
        else:
            result = self._prepare(intent, settings)
        try:
            self.bridge.speak(result.spoken_response, settings.speech_speed)
        except Exception:
            self.logger.exception("Speech output failed")
        try:
            self.history.record_voice_history(intent, result)
            self.history.record_command(text, result, int((time.monotonic() - started) * 1000))
        except Exception:
            self.logger.exception("Could not save command history")
        return self._response(result, intent)

    def _prepare(self, intent: ParsedIntent, settings: Any) -> ActionResult:
        if intent.intent_type == IntentType.UNKNOWN:
            return ActionResult(False, "I did not understand that. Say help for available commands.", intent.intent_type)
        if (intent.intent_type == IntentType.CALL_CONTACT and settings.confirm_before_call) or (intent.intent_type == IntentType.SEND_SMS and settings.confirm_before_message):
            self.pending = intent
            target = intent.entities.get("contact_name", "this contact")
            verb = "Call" if intent.intent_type == IntentType.CALL_CONTACT else "Send the message to"
            return ActionResult(True, f"{verb} {target}? Say yes or no.", intent.intent_type, True)
        return self._execute(intent)

    def _confirm(self) -> ActionResult:
        if self.pending is None:
            return ActionResult(False, "There is no action waiting for confirmation.", IntentType.CONFIRM_ACTION)
        intent, self.pending = self.pending, None
        return self._execute(intent)

    def _execute(self, intent: ParsedIntent) -> ActionResult:
        kind = intent.intent_type
        if kind == IntentType.HELP:
            return ActionResult(True, "You can call, send a message, control torch, brightness, volume, WiFi, Bluetooth, hotspot, camera, or battery.", kind)
        if kind == IntentType.BATTERY_STATUS:
            return ActionResult(True, f"Battery is {self.bridge.get_battery_percentage()} percent.", kind)
        if kind == IntentType.READ_LATEST_SMS:
            return ActionResult(True, self.bridge.read_latest_sms(), kind)
        if kind == IntentType.READ_NOTIFICATIONS:
            return ActionResult(True, self.bridge.read_notification(), kind)
        if kind == IntentType.CALL_CONTACT:
            number = intent.entities.get("phone_number", "")
            if not number:
                return ActionResult(False, f"I need a phone number for {intent.entities.get('contact_name', 'that contact')}.", kind)
            return self._result(self.bridge.call_number(number), f"Calling {intent.entities['contact_name']}.", kind)
        if kind == IntentType.SEND_SMS:
            number = re.sub(r"[^0-9+]", "", intent.entities.get("contact_name", ""))
            if not number:
                return ActionResult(False, f"I need a phone number for {intent.entities.get('contact_name', 'that contact')}.", kind)
            return self._result(self.bridge.send_sms(number, intent.entities.get("message", "")), "Message sent.", kind)
        if kind == IntentType.BRIGHTNESS_SET:
            value = max(0, min(100, int(intent.entities.get("percentage", "50"))))
            return self._result(self.bridge.set_brightness(value), f"Brightness set to {value} percent.", kind)
        actions = {
            IntentType.FLASHLIGHT_ON: (lambda: self.bridge.set_flashlight(True), "Torch on."), IntentType.FLASHLIGHT_OFF: (lambda: self.bridge.set_flashlight(False), "Torch off."),
            IntentType.BRIGHTNESS_UP: (lambda: self.bridge.adjust_brightness("up"), "Brightness settings opened."), IntentType.BRIGHTNESS_DOWN: (lambda: self.bridge.adjust_brightness("down"), "Brightness settings opened."),
            IntentType.VOLUME_UP: (lambda: self.bridge.adjust_volume("up"), "Volume increased."), IntentType.VOLUME_DOWN: (lambda: self.bridge.adjust_volume("down"), "Volume decreased."), IntentType.VOLUME_MUTE: (lambda: self.bridge.adjust_volume("mute"), "Volume muted."),
            IntentType.WIFI_ON: (lambda: self.bridge.set_wifi(True), "WiFi settings opened."), IntentType.WIFI_OFF: (lambda: self.bridge.set_wifi(False), "WiFi settings opened."),
            IntentType.BLUETOOTH_ON: (lambda: self.bridge.set_bluetooth(True), "Bluetooth settings opened."), IntentType.BLUETOOTH_OFF: (lambda: self.bridge.set_bluetooth(False), "Bluetooth settings opened."),
            IntentType.HOTSPOT_ON: (lambda: self.bridge.set_hotspot(True), "Hotspot settings opened."), IntentType.HOTSPOT_OFF: (lambda: self.bridge.set_hotspot(False), "Hotspot settings opened."),
            IntentType.OPEN_CAMERA: (lambda: self.bridge.open_application("camera"), "Camera opened."), IntentType.CLOSE_APP: (lambda: self.bridge.close_application(), "Application closed."),
        }
        if kind in actions:
            operation, text = actions[kind]
            return self._result(operation(), text, kind)
        return ActionResult(False, "That command is not available yet.", kind)

    @staticmethod
    def _result(success: bool, text: str, kind: IntentType) -> ActionResult:
        return ActionResult(bool(success), text if success else "I could not complete that action.", kind)

    @staticmethod
    def _response(result: ActionResult, intent: ParsedIntent | None) -> dict[str, Any]:
        return {"success": result.success, "response": result.spoken_response, "intent": result.intent_type.value, "confidence": intent.confidence if intent else 0.0, "entities": intent.entities if intent else {}, "needs_confirmation": result.needs_confirmation}
