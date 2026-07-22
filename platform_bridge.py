"""Android-specific bridge code kept out of the top-level android namespace."""

from __future__ import annotations

import logging
import threading
from typing import Any


def launch_webview_if_available(url: str) -> None:
    """Open an Android WebView for the local Flask frontend.

    The import of python-for-android's android.runnable happens inside this
    function so desktop tests can import the project safely.
    """

    try:
        from jnius import autoclass  # type: ignore

        from android.runnable import run_on_ui_thread  # type: ignore
    except Exception:
        return

    logger = logging.getLogger("voiceride")

    @run_on_ui_thread
    def _launch() -> None:
        try:
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            WebView = autoclass("android.webkit.WebView")
            WebViewClient = autoclass("android.webkit.WebViewClient")
            activity = PythonActivity.mActivity
            webview = WebView(activity)
            webview.getSettings().setJavaScriptEnabled(True)
            webview.getSettings().setDomStorageEnabled(True)
            webview.setWebViewClient(WebViewClient())
            webview.loadUrl(url)
            activity.setContentView(webview)
        except Exception as exc:
            logger.exception("Failed to launch Android WebView: %s", exc)

    threading.Timer(0.8, _launch).start()


try:
    from jnius import PythonJavaClass, java_method  # type: ignore

    class TtsInitListener(PythonJavaClass):
        __javainterfaces__ = ["android/speech/tts/TextToSpeech$OnInitListener"]
        __javacontext__ = "app"

        def __init__(self, bridge: AndroidNativeBridge) -> None:
            super().__init__()
            self.bridge = bridge

        @java_method("(I)V")
        def onInit(self, status: int) -> None:
            from jnius import autoclass
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            self.bridge._tts_ready = status == TextToSpeech.SUCCESS
            if not self.bridge._tts_ready:
                self.bridge.logger.error("Android TextToSpeech initialisation failed: %s", status)
                return
            self.bridge.logger.info("Android TextToSpeech is ready")
            if self.bridge._tts_pending is not None:
                pending_text, pending_speed = self.bridge._tts_pending
                self.bridge._tts_pending = None
                self.bridge._speak_now(pending_text, pending_speed)

    class SpeechRecognitionListener(PythonJavaClass):
        __javainterfaces__ = ["android/speech/RecognitionListener"]
        __javacontext__ = "app"

        def __init__(self, bridge: AndroidNativeBridge) -> None:
            super().__init__()
            self.bridge = bridge

        @java_method("(Landroid/os/Bundle;)V")
        def onReadyForSpeech(self, _value: Any) -> None:
            with self.bridge._speech_lock:
                self.bridge._speech_state = "listening"
            self.bridge.logger.info("Speech recognizer is ready")

        @java_method("()V")
        def onBeginningOfSpeech(self) -> None:
            pass

        @java_method("(F)V")
        def onRmsChanged(self, _value: float) -> None:
            pass

        @java_method("([B)V")
        def onBufferReceived(self, _value: Any) -> None:
            pass

        @java_method("()V")
        def onEndOfSpeech(self) -> None:
            pass

        @java_method("(I)V")
        def onError(self, code: int) -> None:
            with self.bridge._speech_lock:
                self.bridge._speech_state = "error"
                self.bridge._speech_error = {
                    1: "Speech recognition network error.",
                    2: "Speech recognition network error.",
                    3: "Audio recording failed. Check microphone permission.",
                    5: "Speech recognition is busy. Please try again.",
                    6: "Speech recognition timed out. Please try again.",
                    7: "I did not catch that. Please say it again.",
                    9: "Microphone permission is required to start listening.",
                    13: "Offline speech recognition package is not installed. Please connect to the internet or download offline language data in Google settings."
                }.get(code, f"Speech recognition stopped (error {code}).")

        @java_method("(Landroid/os/Bundle;)V")
        def onResults(self, bundle: Any) -> None:
            recognizer = self.bridge._class("android.speech.SpeechRecognizer")
            values = bundle.getStringArrayList(recognizer.RESULTS_RECOGNITION)
            with self.bridge._speech_lock:
                self.bridge._speech_state = "result"
                self.bridge._speech_result = str(values.get(0)).strip() if values and values.size() else ""
            self.bridge.logger.info("Speech recognizer result: %s", self.bridge._speech_result)

        @java_method("(Landroid/os/Bundle;)V")
        def onPartialResults(self, _value: Any) -> None:
            pass

        @java_method("(ILandroid/os/Bundle;)V")
        def onEvent(self, _event: int, _value: Any) -> None:
            pass
except ImportError:
    class TtsInitListener:  # type: ignore
        def __init__(self, bridge: Any) -> None:
            pass

    class SpeechRecognitionListener:  # type: ignore
        def __init__(self, bridge: Any) -> None:
            pass


class AndroidNativeBridge:
    """Safe wrapper around Android APIs with desktop fallbacks."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self._activity: Any | None = None
        self._autoclass: Any | None = None
        self._speech_recognizer: Any | None = None
        self._speech_listener: Any | None = None
        self._speech_result: str | None = None
        self._speech_error: str | None = None
        self._speech_state = "idle"
        self._tts: Any | None = None
        self._tts_listener: Any | None = None
        self._tts_ready = False
        self._tts_pending: tuple[str, float] | None = None
        self._speech_lock = threading.Lock()
        try:
            from jnius import autoclass  # type: ignore

            PythonService = autoclass("org.kivy.android.PythonService")
            self._autoclass = autoclass
            self._activity = PythonService.mService
            self.logger.info("Android bridge running from foreground service")
        except Exception:
            self.logger.info("Android bridge running in desktop fallback mode")
        self._ensure_tts()

    @property
    def android_available(self) -> bool:
        return self._activity is not None and self._autoclass is not None

    def _ensure_tts(self) -> None:
        if not self.android_available or self._tts is not None:
            return
        try:
            from android.runnable import run_on_ui_thread  # type: ignore

            @run_on_ui_thread
            def init_tts() -> None:
                try:
                    TextToSpeech = self._class("android.speech.tts.TextToSpeech")
                    self._tts_listener = TtsInitListener(self)
                    self._tts = TextToSpeech(self._activity, self._tts_listener)
                except Exception as exc:
                    self.logger.exception("Could not initialise Android TTS: %s", exc)

            init_tts()
        except Exception as exc:
            self.logger.exception("Android TTS init failed: %s", exc)

    def has_permission(self, permission: str) -> bool:
        """Return whether an Android runtime permission has been granted."""
        if not self.android_available:
            return True
        try:
            from android.permissions import check_permission  # type: ignore
            return bool(check_permission(permission))
        except Exception as exc:
            self.logger.exception("Could not check %s permission: %s", permission, exc)
            return False

    def request_permissions(self, permissions: list[str], timeout_seconds: int = 30) -> dict[str, bool]:
        """Request permissions on Android's UI thread and wait for the user's choice."""
        if not self.android_available:
            return {permission: True for permission in permissions}
        missing = [permission for permission in permissions if not self.has_permission(permission)]
        if not missing:
            return {permission: True for permission in permissions}
        try:
            from android.permissions import request_permissions  # type: ignore
            from android.runnable import run_on_ui_thread  # type: ignore
            completed = threading.Event()

            def on_result(_returned_permissions: Any, _grants: Any) -> None:
                completed.set()

            @run_on_ui_thread
            def request_on_ui_thread() -> None:
                request_permissions(missing, on_result)

            request_on_ui_thread()
            if not completed.wait(timeout_seconds):
                self.logger.warning("Timed out waiting for Android permission result: %s", missing)
            return {permission: self.has_permission(permission) for permission in permissions}
        except Exception as exc:
            self.logger.exception("Could not request Android permissions: %s", exc)
            return {permission: self.has_permission(permission) for permission in permissions}

    def speak(self, text: str, speech_speed: float = 1.0) -> None:
        """Speak feedback after Android TextToSpeech has finished initialising."""
        self.logger.info("TTS response: %s | speed=%s", text, speech_speed)
        if not self.android_available or not text:
            return
        try:
            from android.runnable import run_on_ui_thread  # type: ignore

            @run_on_ui_thread
            def speak_on_ui_thread() -> None:
                TextToSpeech = self._class("android.speech.tts.TextToSpeech")
                if self._tts is None:
                    self._tts_pending = (text, speech_speed)
                    self._tts_listener = TtsInitListener(self)
                    self._tts = TextToSpeech(self._activity, self._tts_listener)
                    return
                if not self._tts_ready:
                    self._tts_pending = (text, speech_speed)
                    return
                self._speak_now(text, speech_speed)

            speak_on_ui_thread()
        except Exception as exc:
            self.logger.exception("Android TTS failed: %s", exc)

    def speech_diagnostic(self) -> dict[str, Any]:
        """Run an audible TTS test and expose state to the developer UI."""
        if not self.android_available:
            return {"ok": False, "message": "Android runtime is not available."}
        try:
            self.speak("VoiceRide speech test. If you hear this, text to speech is working.")
            return {"ok": True, "message": "Speech test requested. Check media volume and Android Text to speech output settings if you hear nothing.", "tts_initialized": self._tts_ready}
        except Exception as exc:
            self.logger.exception("TTS diagnostic failed: %s", exc)
            return {"ok": False, "message": f"TTS test failed: {exc}"}

    def _speak_now(self, text: str, speech_speed: float) -> None:
        if self._tts is None or not self._tts_ready:
            return
        TextToSpeech = self._class("android.speech.tts.TextToSpeech")
        HashMap = self._class("java.util.HashMap")
        params = HashMap()
        params.put("streamType", str(3))
        params.put("utteranceId", "voiceride")
        self._tts.setSpeechRate(max(0.7, min(1.3, speech_speed)))
        result = self._tts.speak(text, TextToSpeech.QUEUE_FLUSH, params)
        if result == TextToSpeech.ERROR:
            self.logger.error("Android TextToSpeech rejected speech output")

    def signal_wake(self, mode: str) -> bool:
        if mode == "torch_blink":
            return self._blink_torch()
        if mode == "vibrate" and self.android_available:
            return self._vibrate(180)
        if mode == "sound" and self.android_available:
            return self._play_tone()
        if mode == "screen_flash":
            return self._flash_window_brightness(0.9, 0.25)
        self.logger.info("Wake signal requested: %s", mode)
        return True

    def _play_tone(self) -> bool:
        try:
            ToneGenerator = self._class("android.media.ToneGenerator")
            gen = ToneGenerator(1, 75)
            gen.startTone(ToneGenerator.TONE_PROP_BEEP)
            return True
        except Exception as exc:
            self.logger.exception("Android wake tone failed: %s", exc)
            return False

    def call_number(self, phone_number: str) -> bool:
        if not self.android_available:
            self.logger.info("Desktop call requested for %s", phone_number)
            return True
        try:
            if not self.request_permissions(["android.permission.CALL_PHONE"]).get("android.permission.CALL_PHONE"):
                self.logger.warning("Phone permission was not granted")
                return False
            Intent = self._class("android.content.Intent")
            Uri = self._class("android.net.Uri")
            intent = Intent(Intent.ACTION_CALL, Uri.parse(f"tel:{phone_number}"))
            self._activity.startActivity(intent)
            return True
        except Exception as exc:
            self.logger.exception("Android call failed: %s", exc)
            return False

    def send_sms(self, phone_number: str, message: str) -> bool:
        if not self.android_available:
            self.logger.info("Desktop SMS requested for %s", phone_number)
            return True
        try:
            if not self.request_permissions(["android.permission.SEND_SMS"]).get("android.permission.SEND_SMS"):
                self.logger.warning("SMS permission was not granted")
                return False
            SmsManager = self._class("android.telephony.SmsManager")
            SmsManager.getDefault().sendTextMessage(phone_number, None, message, None, None)
            return True
        except Exception as exc:
            self.logger.exception("Android SMS failed: %s", exc)
            return False

    def open_application(self, package_or_name: str) -> bool:
        if not self.android_available:
            self.logger.info("Desktop app open requested: %s", package_or_name)
            return True
        try:
            Intent = self._class("android.content.Intent")
            MediaStore = self._class("android.provider.MediaStore")
            if package_or_name == "camera":
                if not self.request_permissions(["android.permission.CAMERA"]).get("android.permission.CAMERA"):
                    self.logger.warning("Camera permission was not granted")
                    return False
                intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            else:
                resolved_package = self._resolve_package_name(package_or_name)
                if not resolved_package:
                    return False
                intent = self._activity.getPackageManager().getLaunchIntentForPackage(resolved_package)
                if intent is None:
                    return False
            self._activity.startActivity(intent)
            return True
        except Exception as exc:
            self.logger.exception("Android app launch failed: %s", exc)
            return False

    def close_application(self, package_or_name: str = "") -> bool:
        if not self.android_available:
            self.logger.info("Desktop app close requested: %s", package_or_name or "current")
            return True
        try:
            Intent = self._class("android.content.Intent")
            intent = Intent(Intent.ACTION_MAIN)
            intent.addCategory(Intent.CATEGORY_HOME)
            intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            self._activity.startActivity(intent)
            return True
        except Exception as exc:
            self.logger.exception("Android close app failed: %s", exc)
            return False

    def set_flashlight(self, enabled: bool) -> bool:
        if not self.android_available:
            self.logger.info("Desktop flashlight enabled=%s", enabled)
            return True
        try:
            if not self.request_permissions(["android.permission.CAMERA"]).get("android.permission.CAMERA"):
                self.logger.warning("Camera permission was not granted")
                return False
            Context = self._class("android.content.Context")
            camera_manager = self._activity.getSystemService(Context.CAMERA_SERVICE)
            camera_id = camera_manager.getCameraIdList()[0]
            camera_manager.setTorchMode(camera_id, enabled)
            return True
        except Exception as exc:
            self.logger.exception("Android flashlight failed: %s", exc)
            return False

    def adjust_volume(self, direction: str) -> bool:
        if not self.android_available:
            self.logger.info("Desktop volume adjustment requested: %s", direction)
            return True
        try:
            Context = self._class("android.content.Context")
            AudioManager = self._class("android.media.AudioManager")
            audio = self._activity.getSystemService(Context.AUDIO_SERVICE)
            adjustment = {
                "up": AudioManager.ADJUST_RAISE,
                "down": AudioManager.ADJUST_LOWER,
                "mute": AudioManager.ADJUST_MUTE,
            }.get(direction, AudioManager.ADJUST_SAME)
            audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, adjustment, AudioManager.FLAG_SHOW_UI)
            return True
        except Exception as exc:
            self.logger.exception("Android volume failed: %s", exc)
            return False

    def set_wifi(self, enabled: bool) -> bool:
        self.logger.info("WiFi change requested: %s", enabled)
        if not self.android_available:
            return True
        try:
            if not self.request_permissions(["android.permission.CHANGE_WIFI_STATE"]).get("android.permission.CHANGE_WIFI_STATE"):
                self.logger.warning("Change WiFi permission was not granted")
                return False
            Context = self._class("android.content.Context")
            wifi = self._activity.getSystemService(Context.WIFI_SERVICE)
            return bool(wifi.setWifiEnabled(enabled))
        except Exception as exc:
            self.logger.exception("Android WiFi toggle failed: %s", exc)
            return self._open_settings_panel("android.settings.WIFI_SETTINGS")

    def set_hotspot(self, enabled: bool) -> bool:
        self.logger.info("Hotspot change requested: %s", enabled)
        if not self.android_available:
            return True
        try:
            if not self.request_permissions(["android.permission.CHANGE_WIFI_STATE"]).get("android.permission.CHANGE_WIFI_STATE"):
                self.logger.warning("Change WiFi permission was not granted")
                return False
            Context = self._class("android.content.Context")
            wifi = self._activity.getSystemService(Context.WIFI_SERVICE)
            if hasattr(wifi, "setWifiApEnabled"):
                config = self._class("android.net.wifi.WifiConfiguration")()
                return bool(wifi.setWifiApEnabled(config, enabled))
        except Exception as exc:
            self.logger.exception("Android hotspot toggle failed: %s", exc)
        return self._open_settings_panel("android.settings.WIRELESS_SETTINGS")

    def set_bluetooth(self, enabled: bool) -> bool:
        self.logger.info("Bluetooth change requested: %s", enabled)
        if not self.android_available:
            return True
        try:
            if not self.request_permissions(["android.permission.BLUETOOTH_CONNECT"]).get("android.permission.BLUETOOTH_CONNECT"):
                self.logger.warning("Bluetooth connect permission was not granted")
                return False
            BluetoothAdapter = self._class("android.bluetooth.BluetoothAdapter")
            adapter = BluetoothAdapter.getDefaultAdapter()
            if adapter is None:
                return self._open_settings_panel("android.settings.BLUETOOTH_SETTINGS")
            return bool(adapter.setEnabled(enabled))
        except Exception as exc:
            self.logger.exception("Android Bluetooth toggle failed: %s", exc)
            return self._open_settings_panel("android.settings.BLUETOOTH_SETTINGS")

    def set_brightness(self, percentage: int) -> bool:
        """Set device brightness after the user grants Android special access once."""
        return self._write_system_brightness(max(0, min(100, percentage)))

    def adjust_brightness(self, direction: str) -> bool:
        if not self.android_available:
            self.logger.info("Desktop brightness adjustment requested: %s", direction)
            return True
        try:
            Settings = self._class("android.provider.Settings")
            current = Settings.System.getInt(
                self._activity.getContentResolver(), Settings.System.SCREEN_BRIGHTNESS, 128
            )
            change = 26 if direction == "up" else -26
            return self._write_system_brightness(round((current + change) * 100 / 255))
        except Exception as exc:
            self.logger.exception("Android brightness adjustment failed: %s", exc)
            return False

    def _write_system_brightness(self, percentage: int) -> bool:
        if not self.android_available:
            self.logger.info("Desktop brightness set to %s%%", percentage)
            return True
        try:
            Settings = self._class("android.provider.Settings")
            if not Settings.System.canWrite(self._activity):
                Intent = self._class("android.content.Intent")
                Uri = self._class("android.net.Uri")
                intent = Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS, Uri.parse(f"package:{self._activity.getPackageName()}"))
                self._activity.startActivity(intent)
                self.logger.warning("WRITE_SETTINGS special access is required for device brightness")
                return False
            value = max(1, min(255, round(percentage * 255 / 100)))
            Settings.System.putInt(self._activity.getContentResolver(), Settings.System.SCREEN_BRIGHTNESS, value)
            self._set_window_brightness(percentage)
            return True
        except Exception as exc:
            self.logger.exception("Android brightness set failed: %s", exc)
            return False

    def _set_window_brightness(self, percentage: int) -> bool:
        try:
            from android.runnable import run_on_ui_thread  # type: ignore

            completed = threading.Event()
            result = {"success": False}

            @run_on_ui_thread
            def apply_brightness() -> None:
                try:
                    window = self._activity.getWindow()
                    attributes = window.getAttributes()
                    attributes.screenBrightness = max(0.0, min(1.0, percentage / 100))
                    window.setAttributes(attributes)
                    result["success"] = True
                except Exception as exc:
                    self.logger.exception("Android window brightness failed: %s", exc)
                finally:
                    completed.set()

            apply_brightness()
            completed.wait(timeout=2.0)
            return bool(result["success"])
        except Exception as exc:
            self.logger.exception("Android window brightness failed: %s", exc)
            return False

    def _flash_window_brightness(self, percentage: int = 0.9, duration_seconds: float = 0.25) -> bool:
        if not self.android_available:
            return True
        try:
            import time
            from android.runnable import run_on_ui_thread  # type: ignore

            completed = threading.Event()
            result = {"success": False}

            @run_on_ui_thread
            def apply_flash() -> None:
                try:
                    window = self._activity.getWindow()
                    attributes = window.getAttributes()
                    attributes.screenBrightness = max(0.0, min(1.0, percentage / 100))
                    window.setAttributes(attributes)
                    result["success"] = True
                except Exception as exc:
                    self.logger.exception("Android wake flash failed: %s", exc)
                finally:
                    completed.set()

            apply_flash()
            completed.wait(timeout=2.0)
            if result["success"]:
                time.sleep(duration_seconds)
                return self._restore_window_brightness()
            return False
        except Exception as exc:
            self.logger.exception("Android wake flash failed: %s", exc)
            return False

    def _restore_window_brightness(self) -> bool:
        try:
            from android.runnable import run_on_ui_thread  # type: ignore

            completed = threading.Event()
            result = {"success": False}

            @run_on_ui_thread
            def apply_restore() -> None:
                try:
                    window = self._activity.getWindow()
                    attributes = window.getAttributes()
                    attributes.screenBrightness = -1.0
                    window.setAttributes(attributes)
                    result["success"] = True
                except Exception as exc:
                    self.logger.exception("Android window brightness restore failed: %s", exc)
                finally:
                    completed.set()

            apply_restore()
            completed.wait(timeout=2.0)
            return bool(result["success"])
        except Exception as exc:
            self.logger.exception("Android window brightness restore failed: %s", exc)
            return False

    def _resolve_package_name(self, package_or_name: str) -> str | None:
        if not package_or_name:
            return None
        normalized = package_or_name.strip().lower().replace(" ", "")
        known = {
            "whatsapp": "com.whatsapp",
            "instagram": "com.instagram.android",
            "facebook": "com.facebook.katana",
            "youtube": "com.google.android.youtube",
            "chrome": "com.android.chrome",
            "maps": "com.google.android.apps.maps",
            "gmail": "com.google.android.gm",
            "messages": "com.google.android.apps.messaging",
            "clock": "com.google.android.deskclock",
            "camera": "camera",
            "photos": "com.google.android.apps.photos",
            "music": "com.google.android.music",
            "settings": "android.settings.SETTINGS",
        }
        best_match = known.get(normalized)
        if best_match:
            return best_match
        for candidate, package in known.items():
            if candidate.startswith(normalized) or normalized.startswith(candidate):
                return package
        return None

