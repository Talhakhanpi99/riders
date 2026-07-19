"""Offline parsing and safe action orchestration for VoiceRide."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from rapidfuzz import fuzz, process

from diag_log import log


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
    """Wake-word matching with conservative aliases for common recognizer mistakes."""

    DEFAULT_ALIASES = {"phone": {"phone", "fone", "full"}}

    def __init__(self, default_wake_word: str = "phone") -> None:
        self.default_wake_word = default_wake_word

    def remove_wake_word(self, text: str, wake_word: str | None = None) -> tuple[bool, str]:
        word = (wake_word or self.default_wake_word).strip().lower()
        clean = " ".join((text or "").strip().split())
        if not word:
            return True, clean
        words = clean.split(maxsplit=1)
        if not words:
            return False, clean
        aliases = self.DEFAULT_ALIASES.get(word, {word})
        heard_word = words[0].lower().strip(",.:!")
        if heard_word in aliases or fuzz.ratio(heard_word, word) >= 85:
            return True, words[1].strip() if len(words) > 1 else ""
        return False, clean


class IntentParser:
    """Deterministic English/Roman-Urdu parser; no network dependency."""

    YES = {"yes", "yes please", "haan", "han", "ha", "theek", "theek hai", "confirm", "ok", "okay"}
    NO = {"no", "nahin", "nahi", "cancel", "ruko", "stop"}
    TURN_ON = ("on", "open", "start", "kholo", "khol", "chalao", "karo", "kro")
    TURN_OFF = ("off", "close", "band", "bandh", "stop", "bnd")
    TORCH_WORDS = ("torch", "flashlight", "flash light", "light")
    BRIGHTNESS_WORDS = ("brightness", "roshni", "screen brightness")

    def parse(self, text: str) -> ParsedIntent:
        raw, value = text or "", self._normalise(text or "")
        if not value:
            return ParsedIntent(IntentType.UNKNOWN, raw)
        if value in self.YES:
            return ParsedIntent(IntentType.CONFIRM_ACTION, raw, confidence=.98)
        if value in self.NO:
            return ParsedIntent(IntentType.CANCEL_ACTION, raw, confidence=.98)
        if self._contains_any(value, ("help", "madad", "commands", "kya kar sakte")):
            return ParsedIntent(IntentType.HELP, raw, confidence=.94)
        if "battery" in value:
            return ParsedIntent(IntentType.BATTERY_STATUS, raw, confidence=.97)
        if self._contains_any(value, ("latest sms", "last message", "sms parho", "sms parh")):
            return ParsedIntent(IntentType.READ_LATEST_SMS, raw, confidence=.92)
        if "notification" in value:
            return ParsedIntent(IntentType.READ_NOTIFICATIONS, raw, confidence=.92)
        match = re.search(r"(?:brightness|roshni)\s*(?:to|set|kar do)?\s*(\d{1,3})\s*(?:%|percent)?", value)
        if match:
            return ParsedIntent(IntentType.BRIGHTNESS_SET, raw, {"percentage": match.group(1)}, .96)
        torch = self._action_for(value, self.TORCH_WORDS)
        if torch == "on":
            return ParsedIntent(IntentType.FLASHLIGHT_ON, raw, confidence=.94)
        if torch == "off":
            return ParsedIntent(IntentType.FLASHLIGHT_OFF, raw, confidence=.94)
        brightness = self._brightness_action(value)
        if brightness:
            return ParsedIntent(brightness, raw, confidence=.94)
        volume = self._volume_action(value)
        if volume:
            return ParsedIntent(volume, raw, confidence=.93)
        toggle = self._toggle(value)
        if toggle:
            return ParsedIntent(toggle, raw, confidence=.92)
        if "camera" in value and self._contains_any(value, self.TURN_ON):
            return ParsedIntent(IntentType.OPEN_CAMERA, raw, confidence=.91)
        if self._contains_any(value, ("close app", "band app", "close application", "app band")):
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
    def _contains_any(value: str, terms: tuple[str, ...], fuzzy: bool = False) -> bool:
        if any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", value) for term in terms):
            return True
        if not fuzzy:
            return False
        words = value.split()
        for term in terms:
            term_words = term.split()
            if len("".join(term_words)) < 4:
                continue
            width = len(term_words)
            for index in range(len(words) - width + 1):
                candidate = " ".join(words[index : index + width])
                if fuzz.ratio(candidate, term) >= 85:
                    return True
        return False

    def _action_for(self, value: str, objects: tuple[str, ...]) -> str | None:
        if not self._contains_any(value, objects, fuzzy=True):
            return None
        if self._contains_any(value, self.TURN_OFF):
            return "off"
        if self._contains_any(value, self.TURN_ON):
            return "on"
        return None

    def _brightness_action(self, value: str) -> IntentType | None:
        if not self._contains_any(value, self.BRIGHTNESS_WORDS, fuzzy=True):
            return None
        if self._contains_any(value, ("increase", "up", "barhao", "barha", "barhao", "tez")):
            return IntentType.BRIGHTNESS_UP
        if self._contains_any(value, ("decrease", "down", "kam", "kam karo", "kam kro")):
            return IntentType.BRIGHTNESS_DOWN
        return None

    def _volume_action(self, value: str) -> IntentType | None:
        if not self._contains_any(value, ("volume", "awaz", "sound"), fuzzy=True):
            return None
        if self._contains_any(value, ("mute", "band", "off")):
            return IntentType.VOLUME_MUTE
        if self._contains_any(value, ("increase", "up", "barhao", "barha", "tez")):
            return IntentType.VOLUME_UP
        if self._contains_any(value, ("decrease", "down", "kam")):
            return IntentType.VOLUME_DOWN
        return None

    def _toggle(self, value: str) -> IntentType | None:
        for names, on, off in (("wifi", "wi fi", "wi-fi"), IntentType.WIFI_ON, IntentType.WIFI_OFF), (("bluetooth", "blue tooth"), IntentType.BLUETOOTH_ON, IntentType.BLUETOOTH_OFF), (("hotspot", "hot spot"), IntentType.HOTSPOT_ON, IntentType.HOTSPOT_OFF):
            if self._contains_any(value, names, fuzzy=True):
                if self._contains_any(value, self.TURN_OFF):
                    return off
                if self._contains_any(value, self.TURN_ON):
                    return on
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

class ContactMatcher:
    """Resolve spoken contact names against Android contacts without network access."""

    HONORIFICS = {"bhai", "bhaiya", "bro", "brother", "sir", "jan", "jaan"}

    @classmethod
    def match(cls, spoken_name: str, contacts: list[dict[str, str]]) -> dict[str, str] | None:
        matches = cls.find_matches(spoken_name, contacts)
        return matches[0] if matches else None

    @classmethod
    def find_matches(cls, spoken_name: str, contacts: list[dict[str, str]]) -> list[dict[str, str]]:
        query = cls._normalise_name(spoken_name).strip()
        if not query or not contacts:
            return []

        matches = []
        seen = set()
        for contact in contacts:
            name = contact.get("name", "")
            number = contact.get("phone_number", "")
            if not name or not number:
                continue
            norm_name = cls._normalise_name(name)
            ratio = fuzz.token_set_ratio(query, norm_name)
            is_substring = query in norm_name or norm_name in query

            if is_substring or ratio >= 80:
                key = (name, number)
                if key not in seen:
                    seen.add(key)
                    matches.append({
                        "name": name,
                        "phone_number": number,
                        "score": ratio
                    })
        matches.sort(key=lambda x: x["score"], reverse=True)
        return matches

    @classmethod
    def _normalise_name(cls, value: str) -> str:
        words = re.sub(r"[^\w\s]", " ", value.lower()).split()
        return " ".join(word for word in words if word not in cls.HONORIFICS)

class AssistantService:
    def __init__(self, *, bridge: Any, history: Any, contacts_repository: Any, settings_repository: Any, settings_service: Any, default_wake_word: str, logger: logging.Logger | None = None) -> None:
        self.bridge, self.history = bridge, history
        self.contacts_repository, self.settings_repository = contacts_repository, settings_repository
        self.settings_service = settings_service
        self.logger = logger or logging.getLogger("voiceride")
        self.parser, self.wake_detector, self.pending = IntentParser(), WakeWordDetector(default_wake_word), None
        self._follow_up_until = 0.0

        # Disambiguation and update status properties
        self.pending_contacts = []
        self.waiting_for_contact_selection = False
        self.last_transcript = ""
        self.last_response = ""
        self.last_update_id = 0

    def arm_follow_up(self, timeout_seconds: int) -> None:
        self._follow_up_until = time.monotonic() + max(2, min(15, timeout_seconds))

    def consume_follow_up(self) -> bool:
        active = time.monotonic() <= self._follow_up_until
        self._follow_up_until = 0.0
        return active

    def handle_text(self, text: str, require_wake_word: bool = False, is_offline_service: bool = False) -> dict[str, Any]:
        started, settings = time.monotonic(), self.settings_service.get()
        woke, command = self.wake_detector.remove_wake_word(text, settings.wake_word)
        if require_wake_word and not woke:
            return self._response(ActionResult(False, f"Say {settings.wake_word} first.", IntentType.UNKNOWN), None)
        if woke:
            self.bridge.signal_wake(settings.wake_feedback_mode)

        # Check if we are waiting for contact selection!
        if self.waiting_for_contact_selection and self.pending_contacts:
            result = self._handle_contact_selection(command)
            intent = ParsedIntent(IntentType.CALL_CONTACT, text, confidence=0.95)
        else:
            intent = self.parser.parse(command)
            if intent.intent_type == IntentType.CONFIRM_ACTION:
                result = self._confirm()
            elif intent.intent_type == IntentType.CANCEL_ACTION:
                self.pending, result = None, ActionResult(True, "Cancelled.", intent.intent_type)
                self.waiting_for_contact_selection = False
                self.pending_contacts = []
            else:
                result = self._prepare(intent, settings)

        self.last_transcript = text
        self.last_response = result.spoken_response
        self.last_update_id += 1

        try:
            # Always call native TTS so the background service speaks.
            # In the foreground WebView, app.js also calls speechSynthesis — but native TTS
            # is kept here as a reliable fallback for devices where WebView TTS is silent.
            log("ASSISTANT", "Calling bridge.speak() | response=%r", result.spoken_response[:80])
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

    def _handle_contact_selection(self, command: str) -> ActionResult:
        # Check if the user wants to cancel
        cleaned = command.lower().strip()
        if cleaned in {"cancel", "no", "stop", "nahi", "nahin", "ruko"}:
            self.waiting_for_contact_selection = False
            self.pending_contacts = []
            return ActionResult(True, "Cancelled.", IntentType.CANCEL_ACTION)

        choices = {contact["name"].lower(): contact for contact in self.pending_contacts}
        index_match = None

        # Word maps for numbers
        word_to_idx = {
            "first": 0, "1st": 0, "one": 0, "1": 0, "pehla": 0, "pehli": 0,
            "second": 1, "2nd": 1, "two": 1, "2": 1, "dusra": 1, "dusri": 1,
            "third": 2, "3rd": 2, "three": 2, "3": 2, "tisra": 2, "tisri": 2,
        }
        for word, idx in word_to_idx.items():
            if word in cleaned and idx < len(self.pending_contacts):
                index_match = self.pending_contacts[idx]
                break

        if index_match:
            contact = index_match
        else:
            result = process.extractOne(cleaned, list(choices.keys()), scorer=fuzz.token_set_ratio, score_cutoff=70)
            if result:
                contact = choices[result[0]]
            else:
                contact = None

        if contact:
            self.waiting_for_contact_selection = False
            self.pending_contacts = []
            return self._result(self.bridge.call_number(contact["phone_number"]), f"Calling {contact['name']}.", IntentType.CALL_CONTACT)
        else:
            names_str = ", ".join(c["name"] for c in self.pending_contacts)
            return ActionResult(False, f"I didn't get that. Please say one of: {names_str}, or say cancel.", IntentType.CALL_CONTACT, True)

    def _execute(self, intent: ParsedIntent) -> ActionResult:
        kind = intent.intent_type
        settings = self.settings_service.get()
        if kind == IntentType.HELP:
            return ActionResult(True, "You can call, send a message, control torch, brightness, volume, WiFi, Bluetooth, hotspot, camera, or battery.", kind)
        if kind == IntentType.BATTERY_STATUS:
            return ActionResult(True, f"Battery is {self.bridge.get_battery_percentage()} percent.", kind)
        if kind == IntentType.READ_LATEST_SMS:
            return ActionResult(True, self.bridge.read_latest_sms(), kind)
        if kind == IntentType.READ_NOTIFICATIONS:
            return ActionResult(True, self.bridge.read_notification(), kind)
        if kind == IntentType.CALL_CONTACT:
            spoken_name = intent.entities.get("contact_name", "")
            direct_number = re.sub(r"[^0-9+]", "", intent.entities.get("phone_number", ""))
            if direct_number:
                return self._result(self.bridge.call_number(direct_number), f"Calling {spoken_name or direct_number}.", kind)

            contacts = self.bridge.list_contacts()
            matches = ContactMatcher.find_matches(spoken_name, contacts)

            if not matches:
                return ActionResult(False, f"I could not find {spoken_name or 'that contact'} in your contacts.", kind)

            if len(matches) > 1:
                # Check if the top match is high confidence and much better than the second
                top_score = matches[0]["score"]
                second_score = matches[1]["score"]
                if top_score >= 95 and (top_score - second_score) >= 15:
                    contact = matches[0]
                    try:
                        self.contacts_repository.increment_usage(contact["name"], contact["phone_number"])
                    except Exception:
                        pass
                    return self._result(self.bridge.call_number(contact["phone_number"]), f"Calling {contact['name']}.", kind)

                self.pending_contacts = matches
                self.waiting_for_contact_selection = True
                self.arm_follow_up(settings.wake_timeout_seconds)

                names_list = [c["name"] for c in matches[:4]]
                names_str = " or ".join(names_list)
                return ActionResult(True, f"I found multiple contacts: {names_str}. Which one would you like to call?", kind, True)

            contact = matches[0]
            try:
                self.contacts_repository.increment_usage(contact["name"], contact["phone_number"])
            except Exception:
                pass
            return self._result(self.bridge.call_number(contact["phone_number"]), f"Calling {contact['name']}.", kind)

        if kind == IntentType.SEND_SMS:
            spoken_name = intent.entities.get("contact_name", "")
            message = intent.entities.get("message", "")
            contacts = self.bridge.list_contacts()
            matches = ContactMatcher.find_matches(spoken_name, contacts)

            if not matches:
                return ActionResult(False, f"I could not find {spoken_name or 'that contact'} in your contacts.", kind)

            contact = matches[0]
            try:
                self.contacts_repository.increment_usage(contact["name"], contact["phone_number"])
            except Exception:
                pass
            return self._result(self.bridge.send_sms(contact["phone_number"], message), f"Message sent to {contact['name']}.", kind)

        if kind == IntentType.BRIGHTNESS_SET:
            value = max(0, min(100, int(intent.entities.get("percentage", "50"))))
            return self._result(self.bridge.set_brightness(value), f"Brightness set to {value} percent.", kind)

        actions = {
            IntentType.FLASHLIGHT_ON: (lambda: self.bridge.set_flashlight(True), "Torch on."),
            IntentType.FLASHLIGHT_OFF: (lambda: self.bridge.set_flashlight(False), "Torch off."),
            IntentType.BRIGHTNESS_UP: (lambda: self.bridge.adjust_brightness("up"), "Brightness increased."),
            IntentType.BRIGHTNESS_DOWN: (lambda: self.bridge.adjust_brightness("down"), "Brightness decreased."),
            IntentType.VOLUME_UP: (lambda: self.bridge.adjust_volume("up"), "Volume increased."),
            IntentType.VOLUME_DOWN: (lambda: self.bridge.adjust_volume("down"), "Volume decreased."),
            IntentType.VOLUME_MUTE: (lambda: self.bridge.adjust_volume("mute"), "Volume muted."),
            IntentType.WIFI_ON: (lambda: self.bridge.set_wifi(True), "WiFi controls opened. Android requires you to change it there."),
            IntentType.WIFI_OFF: (lambda: self.bridge.set_wifi(False), "WiFi controls opened. Android requires you to change it there."),
            IntentType.BLUETOOTH_ON: (lambda: self.bridge.set_bluetooth(True), "Bluetooth controls opened. Android requires you to change it there."),
            IntentType.BLUETOOTH_OFF: (lambda: self.bridge.set_bluetooth(False), "Bluetooth controls opened. Android requires you to change it there."),
            IntentType.HOTSPOT_ON: (lambda: self.bridge.set_hotspot(True), "Hotspot controls opened. Android requires you to change it there."),
            IntentType.HOTSPOT_OFF: (lambda: self.bridge.set_hotspot(False), "Hotspot controls opened. Android requires you to change it there."),
            IntentType.OPEN_CAMERA: (lambda: self.bridge.open_application("camera"), "Camera opened."),
            IntentType.CLOSE_APP: (lambda: self.bridge.close_application(), "Application closed."),
        }
        if kind in actions:
            operation, text = actions[kind]
            return self._result(operation(), text, kind)
        return ActionResult(False, "That command is not available yet.", kind)

    def _result(self, bridge_result: tuple[bool, str] | bool, default_success_text: str, kind: IntentType) -> ActionResult:
        if isinstance(bridge_result, tuple):
            success, message = bridge_result
            return ActionResult(success, message, kind)
        return ActionResult(bool(bridge_result), default_success_text if bridge_result else "I could not complete that action.", kind)

    def _resolve_contact(self, entities: dict[str, str]) -> dict[str, str] | None:
        spoken_name = entities.get("contact_name", "")
        direct_number = re.sub(r"[^0-9+]", "", entities.get("phone_number", ""))
        if direct_number:
            return {"name": spoken_name or direct_number, "phone_number": direct_number}
        try:
            match = ContactMatcher.match(spoken_name, self.bridge.list_contacts())
        except Exception:
            self.logger.exception("Could not resolve contact %s", spoken_name)
            return None
        if match is None:
            return None
        try:
            self.contacts_repository.increment_usage(match["name"], match["phone_number"])
        except Exception:
            self.logger.exception("Could not record contact usage")
        return match

    @staticmethod
    def _response(result: ActionResult, intent: ParsedIntent | None) -> dict[str, Any]:
        return {"success": result.success, "response": result.spoken_response, "intent": result.intent_type.value, "confidence": intent.confidence if intent else 0.0, "entities": intent.entities if intent else {}, "needs_confirmation": result.needs_confirmation}
