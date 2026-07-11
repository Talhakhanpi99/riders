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
        from android.runnable import run_on_ui_thread  # type: ignore
        from jnius import autoclass  # type: ignore
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
        self._speech_lock = threading.Lock()
        try:
            from jnius import autoclass  # type: ignore

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            self._autoclass = autoclass
            self._activity = PythonActivity.mActivity
        except Exception:
            self.logger.info("Android bridge running in desktop fallback mode")

    @property
    def android_available(self) -> bool:
        return self._activity is not None and self._autoclass is not None

    def speak(self, text: str, speech_speed: float = 1.0) -> None:
        self.logger.info("TTS response: %s | speed=%s", text, speech_speed)

    def signal_wake(self, mode: str) -> bool:
        if mode == "torch_blink":
            return self._blink_torch()
        if mode == "vibrate" and self.android_available:
            return self._vibrate(180)
        if mode == "screen_flash":
            return self.set_brightness(100)
        self.logger.info("Wake signal requested: %s", mode)
        return True

    def call_number(self, phone_number: str) -> bool:
        if not self.android_available:
            self.logger.info("Desktop call requested for %s", phone_number)
            return True
        try:
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
        if not self.android_available:
            self.logger.info("Desktop brightness set to %s%%", percentage)
            return True
        try:
            window = self._activity.getWindow()
            attributes = window.getAttributes()
            attributes.screenBrightness = max(0.0, min(1.0, percentage / 100))
            window.setAttributes(attributes)
            return True
        except Exception as exc:
            self.logger.exception("Android brightness failed: %s", exc)
            return False

    def adjust_brightness(self, direction: str) -> bool:
        self.logger.info("Brightness adjustment requested: %s", direction)
        return self._open_settings_panel("android.settings.DISPLAY_SETTINGS") if self.android_available else True

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

    def read_latest_sms(self) -> str:
        if self.android_available:
            try:
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
            from android.permissions import Permission, request_permissions  # type: ignore
            from android.runnable import run_on_ui_thread  # type: ignore
            from jnius import PythonJavaClass, java_method  # type: ignore
            request_permissions([Permission.RECORD_AUDIO])
            bridge = self
            class Listener(PythonJavaClass):
                __javainterfaces__ = ["android/speech/RecognitionListener"]
                __javacontext__ = "app"
                @java_method("(Landroid/os/Bundle;)V")
                def onReadyForSpeech(self, _value: Any) -> None: pass
                @java_method("()V")
                def onBeginningOfSpeech(self) -> None: pass
                @java_method("(F)V")
                def onRmsChanged(self, _value: float) -> None: pass
                @java_method("([B)V")
                def onBufferReceived(self, _value: Any) -> None: pass
                @java_method("()V")
                def onEndOfSpeech(self) -> None: pass
                @java_method("(I)V")
                def onError(self, code: int) -> None:
                    with bridge._speech_lock:
                        bridge._speech_error = "No speech was recognised. Please try again." if code == 7 else f"Speech recognition stopped (error {code})."
                @java_method("(Landroid/os/Bundle;)V")
                def onResults(self, bundle: Any) -> None:
                    recognizer = bridge._class("android.speech.SpeechRecognizer")
                    values = bundle.getStringArrayList(recognizer.RESULTS_RECOGNITION)
                    with bridge._speech_lock:
                        bridge._speech_result = str(values.get(0)).strip() if values and values.size() else ""
                @java_method("(Landroid/os/Bundle;)V")
                def onPartialResults(self, _value: Any) -> None: pass
                @java_method("(ILandroid/os/Bundle;)V")
                def onEvent(self, _event: int, _value: Any) -> None: pass
            @run_on_ui_thread
            def begin() -> None:
                SpeechRecognizer = self._class("android.speech.SpeechRecognizer")
                RecognizerIntent = self._class("android.speech.RecognizerIntent")
                if not SpeechRecognizer.isRecognitionAvailable(self._activity):
                    with self._speech_lock: self._speech_error = "Speech recognition is not available on this phone."
                    return
                if self._speech_recognizer is not None: self._speech_recognizer.destroy()
                self._speech_listener = Listener()
                self._speech_recognizer = SpeechRecognizer.createSpeechRecognizer(self._activity)
                self._speech_recognizer.setRecognitionListener(self._speech_listener)
                intent = RecognizerIntent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                intent.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, max(2, min(15, timeout_seconds)) * 1000)
                self._speech_recognizer.startListening(intent)
            with self._speech_lock: self._speech_result, self._speech_error = None, None
            begin()
            return {"started": True, "status": "listening"}
        except Exception as exc:
            self.logger.exception("Could not start speech recognition: %s", exc)
            return {"started": False, "status": "error", "message": "Could not start speech recognition."}

    def consume_speech_result(self) -> dict[str, str]:
        with self._speech_lock:
            if self._speech_result is not None:
                value, self._speech_result = self._speech_result, None
                return {"status": "result", "transcript": value}
            if self._speech_error is not None:
                value, self._speech_error = self._speech_error, None
                return {"status": "error", "message": value}
        return {"status": "listening"}

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
