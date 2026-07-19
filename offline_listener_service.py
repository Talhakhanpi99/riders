"""Offline Vosk microphone listener run as a sticky Android foreground service."""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from diag_log import log

LOG = logging.getLogger("voiceride.listener")
LOOPBACK_ENDPOINT = "http://127.0.0.1:5000/api/offline/transcript"
SAMPLE_RATE = 16_000.0
SERVICE_BUILD = "0.1.5-diag"


def configure_service_logging() -> None:
    if LOG.handlers:
        return
    LOG.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    LOG.addHandler(stream_handler)
    try:
        from jnius import autoclass  # type: ignore

        PythonService = autoclass("org.kivy.android.PythonService")
        log_dir = Path(str(PythonService.mService.getFilesDir().getAbsolutePath())) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_dir / "listener.log", maxBytes=512_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        LOG.addHandler(file_handler)
        log("LISTENER", "Service logging configured | log_dir=%s", log_dir, logger_name="voiceride.listener")
    except Exception as exc:
        log("LISTENER", "Could not create service log file: %s", exc, level=logging.WARNING, logger_name="voiceride.listener")


try:
    from jnius import PythonJavaClass, java_method  # type: ignore

    class ModelCallback(PythonJavaClass):
        __javainterfaces__ = ["org/vosk/android/StorageService$Callback"]
        __javacontext__ = "app"

        def __init__(self, loaded_model: list[object], ready: threading.Event) -> None:
            super().__init__()
            self.loaded_model = loaded_model
            self.ready = ready

        @java_method("(Lorg/vosk/Model;)V")
        def onComplete(self, model: object) -> None:
            self.loaded_model.append(model)
            log("LISTENER", "Vosk model unpack onComplete", logger_name="voiceride.listener")
            self.ready.set()

        @java_method("(Ljava/lang/Exception;)V")
        def onError(self, error: object) -> None:
            log("LISTENER", "Vosk model unpack onError: %s", error, level=logging.ERROR, logger_name="voiceride.listener")
            self.ready.set()

    class RecognitionCallback(PythonJavaClass):
        __javainterfaces__ = ["org/vosk/android/RecognitionListener"]
        __javacontext__ = "app"

        def __init__(self, post_transcript_fn) -> None:
            super().__init__()
            self.post_transcript_fn = post_transcript_fn

        @java_method("(Ljava/lang/String;)V")
        def onPartialResult(self, _hypothesis: str) -> None:
            pass

        @java_method("(Ljava/lang/String;)V")
        def onResult(self, hypothesis: str) -> None:
            transcript = _text_from_hypothesis(hypothesis)
            if transcript:
                log("LISTENER", "Heard transcript: %r", transcript, logger_name="voiceride.listener")
                self.post_transcript_fn(transcript)

        @java_method("(Ljava/lang/String;)V")
        def onFinalResult(self, hypothesis: str) -> None:
            transcript = _text_from_hypothesis(hypothesis)
            if transcript:
                log("LISTENER", "Final transcript: %r", transcript, logger_name="voiceride.listener")
                self.post_transcript_fn(transcript)

        @java_method("(Ljava/lang/Exception;)V")
        def onError(self, error: object) -> None:
            log("LISTENER", "Recognition error: %s", error, level=logging.ERROR, logger_name="voiceride.listener")

        @java_method("()V")
        def onTimeout(self) -> None:
            log("LISTENER", "Recognition timeout; continuing to listen", logger_name="voiceride.listener")
except ImportError:
    class ModelCallback:  # type: ignore
        def __init__(self, loaded_model: list[object], ready: threading.Event) -> None:
            pass

    class RecognitionCallback:  # type: ignore
        def __init__(self, post_transcript_fn: Any) -> None:
            pass


def _post_transcript(transcript: str) -> None:
    payload = json.dumps({"transcript": transcript}).encode("utf-8")
    request = Request(LOOPBACK_ENDPOINT, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        log("LISTENER", "Posting transcript to Flask: %s", LOOPBACK_ENDPOINT, logger_name="voiceride.listener")
        with urlopen(request, timeout=3) as response:
            log("LISTENER", "Flask responded HTTP %s", getattr(response, "status", "?"), logger_name="voiceride.listener")
    except Exception as exc:
        log("LISTENER", "Could not deliver transcript to Flask: %s", exc, level=logging.WARNING, logger_name="voiceride.listener")


def _text_from_hypothesis(hypothesis: str) -> str:
    try:
        return str(json.loads(hypothesis).get("text", "")).strip()
    except (TypeError, ValueError):
        match = re.search(r'"text"\s*:\s*"([^"]*)"', hypothesis or "")
        return match.group(1).strip() if match else ""


def main() -> None:
    """Transcribe locally and hand final text to the existing Flask pipeline."""
    configure_service_logging()
    log("LISTENER", "=== Foreground service main() started | build=%s ===", SERVICE_BUILD, logger_name="voiceride.listener")
    try:
        from jnius import autoclass  # type: ignore
    except Exception:
        log("LISTENER", "jnius unavailable - service cannot run outside APK", level=logging.ERROR, logger_name="voiceride.listener")
        return

    PythonService = autoclass("org.kivy.android.PythonService")
    service = PythonService.mService
    stop_marker = Path(str(service.getFilesDir().getAbsolutePath())) / "offline_listener.stop"
    log("LISTENER", "Service context OK | stop_marker=%s", stop_marker, logger_name="voiceride.listener")

    microphone = "android.permission.RECORD_AUDIO"
    try:
        from android.permissions import check_permission  # type: ignore
        mic_granted = bool(check_permission(microphone))
        log("LISTENER", "Microphone permission granted=%s", mic_granted, logger_name="voiceride.listener")
        if not mic_granted:
            log("LISTENER", "Cannot listen without RECORD_AUDIO permission", level=logging.ERROR, logger_name="voiceride.listener")
            return
    except Exception as exc:
        log("LISTENER", "Could not verify microphone permission: %s", exc, level=logging.WARNING, logger_name="voiceride.listener")

    StorageService = autoclass("org.vosk.android.StorageService")
    Recognizer = autoclass("org.vosk.Recognizer")
    SpeechService = autoclass("org.vosk.android.SpeechService")
    ready = threading.Event()
    loaded_model: list[object] = []

    log("LISTENER", "Unpacking Vosk model from APK assets...", logger_name="voiceride.listener")
    callback = ModelCallback(loaded_model, ready)
    StorageService.unpack(service, "model-en-us", "model-en-us", callback)
    if not ready.wait(45):
        log("LISTENER", "Vosk model unpack timed out after 45s", level=logging.ERROR, logger_name="voiceride.listener")
        return
    if not loaded_model:
        log("LISTENER", "Vosk model unpack failed (empty model)", level=logging.ERROR, logger_name="voiceride.listener")
        return

    stop_marker.unlink(missing_ok=True)
    log("LISTENER", "Vosk model ready - starting SpeechService", logger_name="voiceride.listener")

    recognizer = Recognizer(loaded_model[0], SAMPLE_RATE)
    listener = RecognitionCallback(_post_transcript)
    speech = SpeechService(recognizer, SAMPLE_RATE)
    speech.startListening(listener)
    log("LISTENER", "SpeechService.startListening() active - waiting for wake word/commands", logger_name="voiceride.listener")
    try:
        while not stop_marker.exists():
            time.sleep(1)
    finally:
        log("LISTENER", "Stop marker detected - shutting down service", logger_name="voiceride.listener")
        speech.stop()
        recognizer.close()
        PythonService.mService.stopSelf()
        log("LISTENER", "=== Foreground service stopped ===", logger_name="voiceride.listener")


if __name__ == "__main__":
    main()
