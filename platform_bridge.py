"""Android-specific bridge code kept out of the top-level android namespace."""

from __future__ import annotations

import logging
import threading
from typing import Any

from diag_log import log
from vosk_assets import unpack_model_from_assets, vosk_java_available

WEBVIEW_INSTANCE: Any | None = None


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
        global WEBVIEW_INSTANCE
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
            WEBVIEW_INSTANCE = webview
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
            Locale = autoclass("java.util.Locale")
            self.bridge._tts_ready = status == TextToSpeech.SUCCESS
            log("TTS", "onInit callback | status=%s ready=%s", status, self.bridge._tts_ready)
            if not self.bridge._tts_ready:
                self.bridge.logger.error("Android TextToSpeech initialisation failed: %s", status)
                return
            try:
                self.bridge._tts.setLanguage(Locale.US)
                log("TTS", "Language set to Locale.US")
            except Exception as e:
                self.bridge.logger.exception("Failed to set TTS language to Locale.US: %s", e)
            self.bridge.logger.info("Android TextToSpeech is ready")
            if self.bridge._tts_pending is not None:
                pending_text, pending_speed = self.bridge._tts_pending
                self.bridge._tts_pending = None
                log("TTS", "Flushing pending speech (%d chars)", len(pending_text))
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

    class VoskModelCallback(PythonJavaClass):
        __javainterfaces__ = ["org/vosk/android/StorageService$Callback"]
        __javacontext__ = "app"

        def __init__(self, loaded_model: list[object], ready: threading.Event, logger: Any) -> None:
            super().__init__()
            self.loaded_model = loaded_model
            self.ready = ready
            self.logger = logger

        @java_method("(Lorg/vosk/Model;)V")
        def onComplete(self, model: object) -> None:
            self.loaded_model.append(model)
            self.ready.set()

        @java_method("(Ljava/lang/Exception;)V")
        def onError(self, error: object) -> None:
            log("VOSK", "StorageService.unpack onError: %s", error, level=logging.ERROR)
            self.logger.error("Could not unpack the local Vosk model: %s", error)
            self.ready.set()

    class VoskRecognitionCallback(PythonJavaClass):
        __javainterfaces__ = ["org/vosk/android/RecognitionListener"]
        __javacontext__ = "app"

        def __init__(self, on_result_fn: Any, on_error_fn: Any) -> None:
            super().__init__()
            self.on_result_fn = on_result_fn
            self.on_error_fn = on_error_fn

        @java_method("(Ljava/lang/String;)V")
        def onPartialResult(self, _hypothesis: str) -> None:
            pass

        @java_method("(Ljava/lang/String;)V")
        def onResult(self, hypothesis: str) -> None:
            self.on_result_fn(hypothesis)

        @java_method("(Ljava/lang/String;)V")
        def onFinalResult(self, hypothesis: str) -> None:
            self.on_result_fn(hypothesis)

        @java_method("(Ljava/lang/Exception;)V")
        def onError(self, error: object) -> None:
            self.on_error_fn(error)

        @java_method("()V")
        def onTimeout(self) -> None:
            pass

except ImportError:
    class TtsInitListener:  # type: ignore
        def __init__(self, bridge: Any) -> None:
            pass

    class SpeechRecognitionListener:  # type: ignore
        def __init__(self, bridge: Any) -> None:
            pass

    class VoskModelCallback:  # type: ignore
        def __init__(self, loaded_model: list[object], ready: threading.Event, logger: Any) -> None:
            pass

    class VoskRecognitionCallback:  # type: ignore
        def __init__(self, on_result_fn: Any, on_error_fn: Any) -> None:
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
        self._vosk_model: Any = None
        self._vosk_loading: bool = False
        self._vosk_error: str | None = None
        self._vosk_speech_service: Any = None

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

        if self.android_available:
            log("BRIDGE", "Android bridge ready | activity=%s", type(self._activity).__name__)
            # Start loading Vosk model in a background thread
            threading.Thread(target=self._load_vosk_model, daemon=True, name="vosk-loader").start()

            # Initialize TTS early
            try:
                from jnius import autoclass
                PythonService = autoclass("org.kivy.android.PythonService")
                is_service = isinstance(self._activity, PythonService)
            except Exception:
                is_service = False

            try:
                if not is_service:
                    from android.runnable import run_on_ui_thread  # type: ignore
                    @run_on_ui_thread
                    def init_tts() -> None:
                        try:
                            TextToSpeech = self._class("android.speech.tts.TextToSpeech")
                            self._tts_listener = TtsInitListener(self)
                            self._tts = TextToSpeech(self._activity, self._tts_listener)
                            log("TTS", "TextToSpeech constructor called on UI thread")
                        except Exception as e:
                            self.logger.exception("Failed to initialize TTS on startup: %s", e)
                    init_tts()
                else:
                    TextToSpeech = self._class("android.speech.tts.TextToSpeech")
                    self._tts_listener = TtsInitListener(self)
                    self._tts = TextToSpeech(self._activity, self._tts_listener)
                    log("TTS", "TextToSpeech constructor called from service context")
            except Exception as e:
                self.logger.exception("Failed to schedule TTS initialization: %s", e)
        else:
            log("BRIDGE", "Running in desktop fallback mode (no Android activity)")

    def _load_vosk_model(self) -> None:
        """Load the Vosk speech recognition model.

        Tries in order:
          1. Copy bundled assets into files dir when missing.
          2. Direct Model(path) load from the extracted directory.
          3. StorageService.unpack() when the Java helper is present in the APK.

        Every step is logged so `adb logcat | findstr VOICERIDE` shows what happened.
        """
        if self._vosk_loading:
            log("VOSK", "Load already in progress, skipping duplicate start.")
            return
        self._vosk_loading = True
        self._vosk_error = None
        try:
            log("VOSK", "=== Starting Vosk model load ===")
            available, availability_error = vosk_java_available()
            if not available:
                raise RuntimeError(
                    "Vosk Java classes are missing from the APK. "
                    "Run scripts/fetch_vosk_android.sh before building. "
                    f"Root cause: {availability_error}"
                )

            from jnius import autoclass
            log("VOSK", "org.vosk.Model is available on the classpath")

            files_dir = str(self._activity.getFilesDir().getAbsolutePath())
            model_path = f"{files_dir}/model-en-us"
            log("VOSK", "Looking for model at: %s", model_path)

            import os
            if not os.path.isdir(model_path) or not os.listdir(model_path):
                log("VOSK", "Model directory missing or empty - unpacking bundled assets")
                model_path = unpack_model_from_assets(self._activity, "model-en-us", "model-en-us")

            try:
                Model = autoclass("org.vosk.Model")
                self._vosk_model = Model(model_path)
                log("VOSK", "Model loaded directly from path - SUCCESS")
                return
            except Exception as direct_exc:
                log("VOSK", "Direct Model() load failed: %s - trying StorageService.unpack()", direct_exc, level=logging.WARNING)

            try:
                StorageService = autoclass("org.vosk.android.StorageService")
            except Exception as storage_exc:
                raise RuntimeError(
                    f"Model files are present but org.vosk.Model failed to load: {direct_exc}"
                ) from storage_exc

            log("VOSK", "StorageService loaded - calling unpack('model-en-us', 'model-en-us')")
            ready = threading.Event()
            loaded_model: list[object] = []
            callback = VoskModelCallback(loaded_model, ready, self.logger)
            StorageService.unpack(self._activity, "model-en-us", "model-en-us", callback)
            log("VOSK", "unpack() called - waiting up to 60 seconds for callback...")

            if ready.wait(60):
                if loaded_model:
                    self._vosk_model = loaded_model[0]
                    log("VOSK", "StorageService.unpack() succeeded - model ready")
                else:
                    log("VOSK", "StorageService.unpack() callback fired but model list is empty - onError was called", level=logging.ERROR)
            else:
                log("VOSK", "StorageService.unpack() did not complete within 60 seconds - model NOT loaded", level=logging.ERROR)
        except Exception as exc:
            self._vosk_error = str(exc)
            log("VOSK", "Unexpected error loading Vosk model: %s", exc, level=logging.ERROR)
            self.logger.exception("[VOSK] Unexpected error loading Vosk model: %s", exc)
        finally:
            self._vosk_loading = False
            status = "LOADED" if self._vosk_model is not None else "FAILED"
            log("VOSK", "=== Model load finished: %s ===", status)

    def evaluate_javascript(self, js_code: str) -> None:
        global WEBVIEW_INSTANCE
        if not self.android_available or WEBVIEW_INSTANCE is None:
            return
        try:
            from android.runnable import run_on_ui_thread  # type: ignore
            @run_on_ui_thread
            def _run() -> None:
                try:
                    WEBVIEW_INSTANCE.evaluateJavascript(js_code, None)
                except Exception as exc:
                    self.logger.warning("Failed to evaluate JS: %s", exc)
            _run()
        except Exception as exc:
            self.logger.warning("Could not run JS on UI thread: %s", exc)

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

    def runtime_snapshot(self) -> dict[str, Any]:
        """Expose live subsystem state for diagnostics and adb logcat correlation."""
        return {
            "android_available": self.android_available,
            "vosk_status": "loaded" if self._vosk_model is not None else ("loading" if self._vosk_loading else "failed"),
            "vosk_loading": self._vosk_loading,
            "vosk_error": self._vosk_error,
            "tts_ready": self._tts_ready,
            "tts_pending": self._tts_pending is not None,
            "speech_state": self._speech_state,
        }

    def speak(self, text: str, speech_speed: float = 1.0) -> None:
        """Speak feedback after Android TextToSpeech has finished initialising."""
        preview = (text[:80] + "...") if len(text) > 80 else text
        log("TTS", "speak() called | ready=%s pending=%s speed=%s text=%r", self._tts_ready, self._tts_pending is not None, speech_speed, preview)
        if not self.android_available or not text:
            log("TTS", "speak() skipped | android=%s text_empty=%s", self.android_available, not bool(text), level=logging.WARNING)
            return
        try:
            from android.runnable import run_on_ui_thread  # type: ignore

            @run_on_ui_thread
            def speak_on_ui_thread() -> None:
                TextToSpeech = self._class("android.speech.tts.TextToSpeech")
                if self._tts is None:
                    log("TTS", "TTS engine not created yet - queueing speech and initializing")
                    self._tts_pending = (text, speech_speed)
                    self._tts_listener = TtsInitListener(self)
                    self._tts = TextToSpeech(self._activity, self._tts_listener)
                    return
                if not self._tts_ready:
                    log("TTS", "TTS not ready yet - queueing speech")
                    self._tts_pending = (text, speech_speed)
                    return
                self._speak_now(text, speech_speed)

            speak_on_ui_thread()
        except Exception as exc:
            log("TTS", "speak() failed: %s", exc, level=logging.ERROR)
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
            log("TTS", "_speak_now skipped | tts=%s ready=%s", self._tts is not None, self._tts_ready, level=logging.WARNING)
            return
        TextToSpeech = self._class("android.speech.tts.TextToSpeech")
        Bundle = self._class("android.os.Bundle")
        bundle = Bundle()
        self._tts.setSpeechRate(max(0.7, min(1.3, speech_speed)))
        result = self._tts.speak(text, TextToSpeech.QUEUE_FLUSH, bundle, "voiceride")
        if result == TextToSpeech.ERROR:
            log("TTS", "TextToSpeech.speak() returned ERROR", level=logging.ERROR)
            self.logger.error("Android TextToSpeech rejected speech output")
        else:
            log("TTS", "TextToSpeech.speak() accepted (%d chars)", len(text))
    def signal_wake(self, mode: str) -> bool:
        if mode == "torch_blink":
            return self._blink_torch()
        if mode == "vibrate" and self.android_available:
            return self._vibrate(180)
        if mode == "screen_flash":
            return self._set_window_brightness(100)
        self.logger.info("Wake signal requested: %s", mode)
        return True

    def call_number(self, phone_number: str) -> tuple[bool, str]:
        if not self.android_available:
            self.logger.info("Desktop call requested for %s", phone_number)
            return True, "Call simulated on desktop."
        try:
            if not self.request_permissions(["android.permission.CALL_PHONE"]).get("android.permission.CALL_PHONE"):
                self.logger.warning("Phone permission was not granted")
                return False, "Phone calling permission was denied. Please go to your phone settings, select VoiceRide, and allow the Phone permission."
            Intent = self._class("android.content.Intent")
            Uri = self._class("android.net.Uri")
            intent = Intent(Intent.ACTION_CALL, Uri.parse(f"tel:{phone_number}"))
            self._activity.startActivity(intent)
            return True, f"Placing call to {phone_number}."
        except Exception as exc:
            self.logger.exception("Android call failed: %s", exc)
            return False, f"Android system call failed. Error: {exc}. Please verify if your device has calling capabilities and the SIM card is active."

    def send_sms(self, phone_number: str, message: str) -> tuple[bool, str]:
        if not self.android_available:
            self.logger.info("Desktop SMS requested for %s", phone_number)
            return True, "SMS sending simulated on desktop."
        try:
            if not self.request_permissions(["android.permission.SEND_SMS"]).get("android.permission.SEND_SMS"):
                self.logger.warning("SMS permission was not granted")
                return False, "SMS sending permission was denied. Please allow SMS permission for VoiceRide in your phone settings."
            SmsManager = self._class("android.telephony.SmsManager")
            SmsManager.getDefault().sendTextMessage(phone_number, None, message, None, None)
            return True, "Message sent successfully."
        except Exception as exc:
            self.logger.exception("Android SMS failed: %s", exc)
            return False, f"SMS sending failed. Error: {exc}. Please check your SIM network status, cellular balance, or try restarting the app."

    def open_application(self, package_or_name: str) -> tuple[bool, str]:
        if not self.android_available:
            self.logger.info("Desktop app open requested: %s", package_or_name)
            return True, f"Application {package_or_name} opening simulated on desktop."
        try:
            Intent = self._class("android.content.Intent")
            MediaStore = self._class("android.provider.MediaStore")
            if package_or_name == "camera":
                if not self.request_permissions(["android.permission.CAMERA"]).get("android.permission.CAMERA"):
                    self.logger.warning("Camera permission was not granted")
                    return False, "Camera permission was denied. Please allow Camera permission in settings to open the camera."
                intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            else:
                intent = self._activity.getPackageManager().getLaunchIntentForPackage(package_or_name)
                if intent is None:
                    return False, f"Application {package_or_name} is not installed on this device. Please check the package name."
            self._activity.startActivity(intent)
            return True, f"Opened {package_or_name}."
        except Exception as exc:
            self.logger.exception("Android app launch failed: %s", exc)
            return False, f"Failed to open {package_or_name}. Error: {exc}."

    def close_application(self, package_or_name: str = "") -> tuple[bool, str]:
        if not self.android_available:
            self.logger.info("Desktop app close requested: %s", package_or_name or "current")
            return True, "Application close simulated on desktop."
        try:
            Intent = self._class("android.content.Intent")
            intent = Intent(Intent.ACTION_MAIN)
            intent.addCategory(Intent.CATEGORY_HOME)
            intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            self._activity.startActivity(intent)
            return True, "Returned to home screen."
        except Exception as exc:
            self.logger.exception("Android close app failed: %s", exc)
            return False, f"Failed to close application. Error: {exc}."

    def set_flashlight(self, enabled: bool) -> tuple[bool, str]:
        if not self.android_available:
            self.logger.info("Desktop flashlight enabled=%s", enabled)
            return True, f"Flashlight simulated {'on' if enabled else 'off'} on desktop."
        try:
            if not self.request_permissions(["android.permission.CAMERA"]).get("android.permission.CAMERA"):
                self.logger.warning("Camera permission was not granted")
                return False, "Camera permission is denied, which is required to control the flashlight. Please allow Camera access for VoiceRide in your Android settings."
            Context = self._class("android.content.Context")
            camera_manager = self._activity.getSystemService(Context.CAMERA_SERVICE)
            camera_id = camera_manager.getCameraIdList()[0]
            camera_manager.setTorchMode(camera_id, enabled)
            return True, f"Torch turned {'on' if enabled else 'off'}."
        except Exception as exc:
            self.logger.exception("Android flashlight failed: %s", exc)
            return False, f"Could not control flashlight. Error: {exc}. This might happen if another app (like the Camera app) is currently using the camera, or if your device does not support torch controls."

    def adjust_volume(self, direction: str) -> tuple[bool, str]:
        if not self.android_available:
            self.logger.info("Desktop volume adjustment requested: %s", direction)
            return True, f"Volume adjustment to {direction} simulated on desktop."
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
            return True, f"Volume adjusted {direction}."
        except Exception as exc:
            self.logger.exception("Android volume failed: %s", exc)
            return False, f"Failed to adjust volume. Error: {exc}."

    def set_wifi(self, enabled: bool) -> tuple[bool, str]:
        self.logger.info("WiFi change requested: %s", enabled)
        if not self.android_available:
            return True, "WiFi settings simulation."
        success = self._open_settings_panel("android.settings.WIFI_SETTINGS")
        if success:
            return True, "I have opened the WiFi settings page. Android security requires you to toggle WiFi manually here."
        return False, "Failed to open WiFi settings page."

    def set_hotspot(self, enabled: bool) -> tuple[bool, str]:
        self.logger.info("Hotspot change requested: %s", enabled)
        if not self.android_available:
            return True, "Hotspot settings simulation."
        success = self._open_settings_panel("android.settings.WIRELESS_SETTINGS")
        if success:
            return True, "I have opened the Hotspot settings page. Android security requires you to toggle Hotspot manually here."
        return False, "Failed to open Hotspot settings page."

    def set_bluetooth(self, enabled: bool) -> tuple[bool, str]:
        self.logger.info("Bluetooth change requested: %s", enabled)
        if not self.android_available:
            return True, "Bluetooth settings simulation."
        success = self._open_settings_panel("android.settings.BLUETOOTH_SETTINGS")
        if success:
            return True, "I have opened the Bluetooth settings page. Android security requires you to toggle Bluetooth manually here."
        return False, "Failed to open Bluetooth settings page."

    def set_brightness(self, percentage: int) -> tuple[bool, str]:
        """Set device brightness after the user grants Android special access once."""
        return self._write_system_brightness(max(0, min(100, percentage)))

    def adjust_brightness(self, direction: str) -> tuple[bool, str]:
        if not self.android_available:
            self.logger.info("Desktop brightness adjustment requested: %s", direction)
            return True, f"Brightness adjustment {direction} simulated on desktop."
        try:
            Settings = self._class("android.provider.Settings")
            current = Settings.System.getInt(
                self._activity.getContentResolver(), Settings.System.SCREEN_BRIGHTNESS, 128
            )
            change = 26 if direction == "up" else -26
            new_percentage = round((current + change) * 100 / 255)
            return self._write_system_brightness(new_percentage)
        except Exception as exc:
            self.logger.exception("Android brightness adjustment failed: %s", exc)
            return False, f"Failed to adjust brightness. Error: {exc}."

    def _write_system_brightness(self, percentage: int) -> tuple[bool, str]:
        if not self.android_available:
            self.logger.info("Desktop brightness set to %s%%", percentage)
            return True, f"Brightness set to {percentage} percent simulated."
        try:
            Settings = self._class("android.provider.Settings")
            if not Settings.System.canWrite(self._activity):
                Intent = self._class("android.content.Intent")
                Uri = self._class("android.net.Uri")
                intent = Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS, Uri.parse(f"package:{self._activity.getPackageName()}"))
                self._activity.startActivity(intent)
                self.logger.warning("WRITE_SETTINGS special access is required for device brightness")
                return False, "This action requires system write settings permission to change screen brightness. I have opened the settings screen, please toggle on 'Allow modifying system settings' for VoiceRide, then try the command again."
            value = max(1, min(255, round(percentage * 255 / 100)))
            Settings.System.putInt(self._activity.getContentResolver(), Settings.System.SCREEN_BRIGHTNESS, value)
            self._set_window_brightness(percentage)
            return True, f"Brightness set to {percentage} percent."
        except Exception as exc:
            self.logger.exception("Android brightness set failed: %s", exc)
            return False, f"Could not change system brightness. Error: {exc}."

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
        """Run one short Android recognition session using the offline Vosk engine."""
        if not self.android_available:
            return {"started": False, "status": "unavailable", "message": "Speech recognition is available on Android only."}
        microphone = "android.permission.RECORD_AUDIO"
        if not self.request_permissions([microphone]).get(microphone):
            return {"started": False, "status": "permission_denied", "message": "Microphone permission is required to start listening."}

        if self._vosk_model is None:
            if self._vosk_loading:
                log("MIC", "Vosk model still loading - microphone request delayed")
                return {"started": False, "status": "loading", "message": "Offline speech engine is initializing. Please wait a moment."}
            if self._vosk_error:
                log("MIC", "Vosk model unavailable after load failure: %s", self._vosk_error, level=logging.ERROR)
                return {"started": False, "status": "error", "message": f"Offline speech engine failed to start: {self._vosk_error}"}
            log("MIC", "Vosk model not loaded - starting background load now")
            threading.Thread(target=self._load_vosk_model, daemon=True, name="vosk-loader-retry").start()
            return {"started": False, "status": "loading", "message": "Offline speech engine is starting up. Please try again in a few seconds."}

        try:
            from jnius import autoclass
            Recognizer = autoclass("org.vosk.Recognizer")
            SpeechService = autoclass("org.vosk.android.SpeechService")
            log("MIC", "Starting Vosk SpeechService listening session (timeout=%ss)", timeout_seconds)

            with self._speech_lock:
                self._speech_result, self._speech_error = None, None
                self._speech_state = "listening"

                if self._vosk_speech_service is not None:
                    try:
                        self._vosk_speech_service.stop()
                    except Exception:
                        pass
                    self._vosk_speech_service = None

                recognizer = Recognizer(self._vosk_model, 16000.0)

                def on_result(hypothesis: str) -> None:
                    import json
                    try:
                        text = json.loads(hypothesis).get("text", "").strip()
                    except Exception:
                        text = ""
                    if text:
                        with self._speech_lock:
                            self._speech_result = text
                            self._speech_state = "result"
                        try:
                            self._vosk_speech_service.stop()
                        except Exception:
                            pass

                def on_error(error: Any) -> None:
                    with self._speech_lock:
                        self._speech_error = f"Offline speech recognition error: {error}"
                        self._speech_state = "error"

                listener = VoskRecognitionCallback(on_result, on_error)
                self._vosk_speech_service = SpeechService(recognizer, 16000.0)
                self._vosk_speech_service.startListening(listener)
                log("MIC", "Vosk SpeechService.startListening() succeeded")

            def safety_timeout() -> None:
                with self._speech_lock:
                    if self._speech_state == "listening":
                        self._speech_state = "error"
                        self._speech_error = "Speech recognition timed out."
                        try:
                            self._vosk_speech_service.stop()
                        except Exception:
                            pass
            threading.Timer(float(timeout_seconds), safety_timeout).start()
            return {"started": True, "status": "listening"}
        except Exception as exc:
            log("MIC", "Could not start Vosk listening: %s", exc, level=logging.ERROR)
            self.logger.exception("Could not start offline Vosk listening: %s", exc)
            return {"started": False, "status": "error", "message": f"Could not start local offline listener: {exc}"}

    def start_offline_listener(self) -> dict[str, Any]:
        """Start the bundled Vosk foreground service from the visible activity."""
        log("SERVICE", "start_offline_listener() called")
        if not self.android_available:
            log("SERVICE", "Android not available - cannot start service", level=logging.WARNING)
            return {"started": False, "status": "unavailable", "message": "Offline listening is available on Android only."}
        microphone = "android.permission.RECORD_AUDIO"
        if not self.request_permissions([microphone]).get(microphone):
            log("SERVICE", "Microphone permission denied", level=logging.WARNING)
            return {"started": False, "status": "permission_denied", "message": "Microphone permission is required."}
        try:
            from pathlib import Path
            files_dir = str(self._activity.getFilesDir().getAbsolutePath())
            stop_marker = Path(files_dir, "offline_listener.stop")
            stop_marker.unlink(missing_ok=True)
            log("SERVICE", "Stop marker cleared from: %s", stop_marker)

            completed = threading.Event()
            error_container: list[Exception] = []
            class_name_used: list[str] = []

            from android.runnable import run_on_ui_thread  # type: ignore

            @run_on_ui_thread
            def run_on_android_ui_thread() -> None:
                try:
                    package_name = self._activity.getPackageName()
                    log("SERVICE", "Package name: %s", package_name)

                    try:
                        from android import start_service  # type: ignore
                        log("SERVICE", "Trying android.start_service('Listener')")
                        start_service("Listener")
                        class_name_used.append("android.start_service('Listener')")
                        log("SERVICE", "android.start_service('Listener') succeeded")
                        return
                    except Exception as module_exc:
                        log("SERVICE", "android.start_service failed: %s", module_exc, level=logging.WARNING)

                    target_class = f"{package_name}.ServiceListener"
                    class_name_used.append(target_class)
                    log("SERVICE", "Trying autoclass(%s)", target_class)
                    from jnius import autoclass
                    ServiceClass = autoclass(target_class)
                    log("SERVICE", "Class loaded - calling ServiceClass.start()")
                    ServiceClass.start(self._activity, "")
                    log("SERVICE", "ServiceClass.start() succeeded")
                except Exception as e:
                    log("SERVICE", "Failed to start service: %s", e, level=logging.ERROR)
                    self.logger.exception("[SERVICE] Failed to start service: %s", e)
                    if class_name_used:
                        log("SERVICE", "Methods tried: %s", class_name_used, level=logging.ERROR)
                    error_container.append(e)
                finally:
                    completed.set()

            run_on_android_ui_thread()
            log("SERVICE", "Waiting for service start on UI thread (10s timeout)...")

            if not completed.wait(timeout=10.0):
                log("SERVICE", "Timed out waiting for service start on UI thread", level=logging.ERROR)
                return {"started": False, "status": "error", "message": "Timeout starting offline listening. Check that the app was built with the Listener service registered in buildozer.spec."}

            if error_container:
                raise error_container[0]

            log("SERVICE", "start_offline_listener() completed successfully")
            return {"started": True, "status": "starting", "message": "Background assistant is starting. You will hear a response when it is ready."}
        except Exception as exc:
            log("SERVICE", "Could not start offline listener: %s", exc, level=logging.ERROR)
            self.logger.exception("[SERVICE] Could not start offline listener: %s", exc)
            err_msg = str(exc)
            if "ClassNotFoundException" in err_msg or "ClassNotFound" in err_msg:
                hint = f"The service class was not found: {err_msg}. This usually means the APK was not built with 'services = Listener:offline_listener_service.py:foreground:sticky' in buildozer.spec, or the build is outdated."
            elif "SecurityException" in err_msg:
                hint = f"Android blocked the service start: {err_msg}. FOREGROUND_SERVICE permission may be missing."
            else:
                hint = f"Service start failed: {err_msg}"
            return {"started": False, "status": "error", "message": hint}


    def stop_offline_listener(self) -> dict[str, Any]:
        log("SERVICE", "stop_offline_listener() called")
        if not self.android_available:
            return {"stopped": True, "status": "unavailable"}
        try:
            from pathlib import Path
            stop_path = Path(str(self._activity.getFilesDir().getAbsolutePath()), "offline_listener.stop")
            stop_path.touch()
            log("SERVICE", "Stop marker written: %s", stop_path)
            return {"stopped": True, "status": "stopping", "message": "Offline listener is stopping."}
        except Exception as exc:
            log("SERVICE", "Could not stop offline listener: %s", exc, level=logging.ERROR)
            self.logger.exception("Could not stop offline listener: %s", exc)
            return {"stopped": False, "status": "error", "message": "Could not stop offline listening."}
    def consume_speech_result(self) -> dict[str, str]:
        with self._speech_lock:
            if self._speech_result is not None:
                value, self._speech_result = self._speech_result, None
                self._speech_state = "idle"
                log("MIC", "Speech result consumed: %r", value)
                return {"status": "result", "transcript": value}
            if self._speech_error is not None:
                value, self._speech_error = self._speech_error, None
                self._speech_state = "idle"
                log("MIC", "Speech error consumed: %s", value, level=logging.WARNING)
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


