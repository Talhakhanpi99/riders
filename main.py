"""VoiceRide Android entry point.

Starts the local Flask app in a background thread, then opens it in an
Android WebView when running inside the APK. On desktop it simply runs Flask.
"""

from __future__ import annotations

import threading
from typing import Any

from app import create_app, configure_logging
from diag_log import log
from platform_bridge import launch_webview_if_available

WEB_URL = "http://127.0.0.1:5000"
DIAG_BUILD = "0.1.5-diag"


def run_flask() -> None:
    configure_logging()
    log("STARTUP", "Flask thread starting | build=%s", DIAG_BUILD)
    flask_app = create_app()
    log("STARTUP", "Flask app created | endpoints ready on %s", WEB_URL)
    flask_app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)


def main() -> None:
    try:
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.uix.label import Label
    except Exception:
        run_flask()
        return

    configure_logging()
    log("STARTUP", "Kivy entry | build=%s | opening WebView at %s", DIAG_BUILD, WEB_URL)
    threading.Thread(target=run_flask, daemon=True).start()

    class VoiceRideApp(App):
        def build(self) -> Any:
            Clock.schedule_once(lambda _dt: launch_webview_if_available(WEB_URL), 1.0)
            return Label(text="Starting VoiceRide...")

    VoiceRideApp().run()


if __name__ == "__main__":
    main()
