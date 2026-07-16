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
                    7: "No speech was recognised. Please try again.",
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

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            self._autoclass = autoclass
            self._activity = PythonActivity.mActivity
        except Exception:
            # A python-for-android service has no PythonActivity, but it can
            # still use a Service as an Android Context (notably for TTS).
            try:
                from jnius import autoclass  # type: ignore

                PythonService = autoclass("org.kivy.android.PythonService")
                self._autoclass = autoclass
                self._activity = PythonService.mService
                self.logger.info("Android bridge running from foreground service")
            except Exception:
                self.logger.info("Android bridge running in desktop fallback mode")

    @property
    def android_available(self) -> bool:
        return self._activity is not None and self._autoclass is not None

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
        Bundle = self._class("android.os.Bundle")
        bundle = Bundle()
        self._tts.setSpeechRate(max(0.7, min(1.3, speech_speed)))
        result = self._tts.speak(text, TextToSpeech.QUEUE_FLUSH, bundle, "voiceride")
        if result == TextToSpeech.ERROR:
            self.logger.error("Android TextToSpeech rejected speech output")
    def signal_wake(self, mode: str) -> bool:
        if mode == "torch_blink":
            return self._blink_torch()
        if mode == "vibrate" and self.android_available:
            return self._vibrate(180)
        if mode == "screen_flash":
            return self._set_window_brightness(100)
        self.logger.info("Wake signal requested: %s", mode)
        return True

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
                intent = self._activity.getPackageManager().getLaunchIntentForPackage(package_or_name)
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
        return self._open_settings_panel("android.settings.WIFI_SETTINGS") if self.android_available else True

    def set_hotspot(self, enabled: bool) -> bool:
        self.logger.info("Hotspot change requested: %s", enabled)
        return self._open_settings_panel("android.settings.WIRELESS_SETTINGS") if self.android_available else True

    def set_bluetooth(self, enabled: bool) -> bool:
        self.logger.info("Bluetooth change requested: %s", enabled)
        return self._open_settings_panel("android.settings.BLUETOOTH_SETTINGS") if self.android_available else True

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
            window = self._activity.getWindow()
            attributes = window.getAttributes()
            attributes.screenBrightness = max(0.0, min(1.0, percentage / 100))
            window.setAttributes(attributes)
            return True
        except Exception as exc:
            self.logger.exception("Android window brightness failed: %s", exc)
            return False
    def get_battery_percentage(self) -> int:
        if self.android_available:
            try:
                Intent = self._class("android.content.Intent")
                IntentFilter = self._class("android.content.IntentFilter")
                battery = self._activity.registerReceiver(None, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
                level = battery.getIntExtra("level", -1)
                scale = battery.getIntExtra("scale", -1)
                if level >= 0 and scale > 0:
                    return int(level * 100 / scale)
            except Exception as exc:
                self.logger.exception("Android battery read failed: %s", exc)
        return 64

    def list_contacts(self) -> list[dict[str, str]]:
        """Return local phone contacts for offline fuzzy name resolution."""
        if not self.android_available:
            return []
        try:
            permission = "android.permission.READ_CONTACTS"
            if not self.request_permissions([permission]).get(permission):
                self.logger.warning("Contacts permission was not granted")
                return []
            ContactsContract = self._class("android.provider.ContactsContract$CommonDataKinds$Phone")
            cursor = self._activity.getContentResolver().query(
                ContactsContract.CONTENT_URI, None, None, None, "display_name COLLATE NOCASE ASC"
            )
            contacts: list[dict[str, str]] = []
            if cursor is None:
                return contacts
            name_index = cursor.getColumnIndex("display_name")
            number_index = cursor.getColumnIndex("data1")
            while cursor.moveToNext():
                name = cursor.getString(name_index)
                number = cursor.getString(number_index)
                if name and number:
                    contacts.append({"name": str(name), "phone_number": str(number)})
            cursor.close()
            return contacts
        except Exception as exc:
            self.logger.exception("Android contacts lookup failed: %s", exc)
            return []
    def read_latest_sms(self) -> str:
        if self.android_available:
            try:
                if not self.request_permissions(["android.permission.READ_SMS"]).get("android.permission.READ_SMS"):
                    self.logger.warning("SMS read permission was not granted")
                    return "SMS permission is required to read messages."
                Uri = self._class("android.net.Uri")
                cursor = self._activity.getContentResolver().query(
                    Uri.parse("content://sms/inbox"),
                    None,
                    None,
                    None,
                    "date DESC",
                )
                if cursor and cursor.moveToFirst():
                    body = cursor.getString(cursor.getColumnIndex("body"))
                    cursor.close()
                    return body
            except Exception as exc:
                self.logger.exception("Android SMS read failed: %s", exc)
        return "No unread SMS messages."

    def read_notification(self, package_name: str | None = None) -> str:
        if package_name:
            return f"No unread notifications for {package_name}."
        return "You have no unread notifications."

    def start_listening(self, timeout_seconds: int = 5) -> dict[str, Any]:
        """Run one short Android recognition session; the microphone is not kept open."""
        if not self.android_available:
            return {"started": False, "status": "unavailable", "message": "Speech recognition is available on Android only."}
        try:
            from android.runnable import run_on_ui_thread  # type: ignore
            microphone = "android.permission.RECORD_AUDIO"
            if not self.request_permissions([microphone]).get(microphone):
                return {"started": False, "status": "permission_denied", "message": "Microphone permission is required to start listening."}
            @run_on_ui_thread
            def begin() -> None:
                SpeechRecognizer = self._class("android.speech.SpeechRecognizer")
                RecognizerIntent = self._class("android.speech.RecognizerIntent")
                Intent = self._class("android.content.Intent")
                if not SpeechRecognizer.isRecognitionAvailable(self._activity):
                    with self._speech_lock:
                        self._speech_state = "error"
                        self._speech_error = "Speech recognition is not available on this phone."
                    return
                if self._speech_recognizer is not None:
                    self._speech_recognizer.destroy()
                self._speech_listener = SpeechRecognitionListener(self)
                self._speech_recognizer = SpeechRecognizer.createSpeechRecognizer(self._activity)
                self._speech_recognizer.setRecognitionListener(self._speech_listener)
                intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                intent.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, max(2, min(15, timeout_seconds)) * 1000)
                self._speech_recognizer.startListening(intent)
            with self._speech_lock:
                self._speech_result, self._speech_error = None, None
                self._speech_state = "starting"
            begin()
            def fail_if_not_ready() -> None:
                with self._speech_lock:
                    if self._speech_state != "starting":
                        return
                    self._speech_state = "error"
                    self._speech_error = "Android did not initialize speech recognition. Check the phone speech service and try again."
                self.logger.error("Speech recognizer never reached onReadyForSpeech")
            threading.Timer(4.0, fail_if_not_ready).start()
            return {"started": True, "status": "starting"}
        except Exception as exc:
            self.logger.exception("Could not start speech recognition: %s", exc)
            return {"started": False, "status": "error", "message": "Could not start speech recognition."}

    def start_offline_listener(self) -> dict[str, Any]:
        """Start the bundled Vosk foreground service from the visible activity."""
        if not self.android_available:
            return {"started": False, "status": "unavailable", "message": "Offline listening is available on Android only."}
        microphone = "android.permission.RECORD_AUDIO"
        if not self.request_permissions([microphone]).get(microphone):
            return {"started": False, "status": "permission_denied", "message": "Microphone permission is required."}
        try:
            from pathlib import Path
            Path(str(self._activity.getFilesDir().getAbsolutePath()), "offline_listener.stop").unlink(missing_ok=True)
            
            import threading
            completed = threading.Event()
            error_container: list[Exception] = []
            
            def run_on_main_thread(dt: float) -> None:
                try:
                    from jnius import autoclass
                    package_name = self._activity.getPackageName()
                    ServiceClass = autoclass(f"{package_name}.ServiceListener")
                    ServiceClass.start(self._activity, "")
                except Exception as e:
                    error_container.append(e)
                finally:
                    completed.set()
            
            from kivy.clock import Clock
            Clock.schedule_once(run_on_main_thread)
            
            if not completed.wait(timeout=5.0):
                return {"started": False, "status": "error", "message": "Timeout starting offline listening on main thread."}
            
            if error_container:
                raise error_container[0]
                
            return {"started": True, "status": "starting", "message": "Offline listening is starting."}
        except Exception as exc:
            self.logger.exception("Could not start offline listener: %s", exc)
            return {"started": False, "status": "error", "message": f"Could not start offline listening: {exc}"}

    def stop_offline_listener(self) -> dict[str, Any]:
        if not self.android_available:
            return {"stopped": True, "status": "unavailable"}
        try:
            from pathlib import Path
            Path(str(self._activity.getFilesDir().getAbsolutePath()), "offline_listener.stop").touch()
            return {"stopped": True, "status": "stopping", "message": "Offline listener is stopping."}
        except Exception as exc:
            self.logger.exception("Could not stop offline listener: %s", exc)
            return {"stopped": False, "status": "error", "message": "Could not stop offline listening."}
    def consume_speech_result(self) -> dict[str, str]:
        with self._speech_lock:
            if self._speech_result is not None:
                value, self._speech_result = self._speech_result, None
                self._speech_state = "idle"
                return {"status": "result", "transcript": value}
            if self._speech_error is not None:
                value, self._speech_error = self._speech_error, None
                self._speech_state = "idle"
                return {"status": "error", "message": value}
            return {"status": self._speech_state}

    def open_airplane_mode_settings(self) -> bool:
        return self._open_settings_panel("android.settings.AIRPLANE_MODE_SETTINGS") if self.android_available else True
    def _class(self, name: str) -> Any:
        if not self._autoclass:
            raise RuntimeError("Android runtime is unavailable")
        return self._autoclass(name)

    def _open_settings_panel(self, action: str) -> bool:
        try:
            Intent = self._class("android.content.Intent")
            self._activity.startActivity(Intent(action))
            return True
        except Exception as exc:
            self.logger.exception("Android settings panel failed: %s", exc)
            return False

    def _blink_torch(self) -> bool:
        import time

        if not self.set_flashlight(True):
            return False
        time.sleep(0.15)
        return self.set_flashlight(False)

    def _vibrate(self, milliseconds: int) -> bool:
        try:
            Context = self._class("android.content.Context")
            vibrator = self._activity.getSystemService(Context.VIBRATOR_SERVICE)
            vibrator.vibrate(milliseconds)
            return True
        except Exception as exc:
            self.logger.exception("Android vibration failed: %s", exc)
            return False

