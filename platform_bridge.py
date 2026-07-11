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
