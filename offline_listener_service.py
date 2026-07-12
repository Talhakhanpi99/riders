"""Offline Vosk microphone listener run as a sticky Android foreground service."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

LOG = logging.getLogger("voiceride.listener")
LOOPBACK_ENDPOINT = "http://127.0.0.1:5000/api/offline/transcript"
SAMPLE_RATE = 16_000.0


def _post_transcript(transcript: str) -> None:
    payload = json.dumps({"transcript": transcript}).encode("utf-8")
    request = Request(LOOPBACK_ENDPOINT, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=3):
            pass
    except Exception as exc:
        LOG.warning("Could not deliver offline transcript: %s", exc)


def _text_from_hypothesis(hypothesis: str) -> str:
    try:
        return str(json.loads(hypothesis).get("text", "")).strip()
    except (TypeError, ValueError):
        match = re.search(r'"text"\s*:\s*"([^"]*)"', hypothesis or "")
        return match.group(1).strip() if match else ""


def main() -> None:
    """Transcribe locally and hand final text to the existing Flask pipeline."""
    try:
        from jnius import PythonJavaClass, autoclass, java_method  # type: ignore
    except Exception:
        LOG.exception("Offline listener is available only inside the Android APK")
        return

    PythonService = autoclass("org.kivy.android.PythonService")
    stop_marker = Path(str(PythonService.mService.getFilesDir().getAbsolutePath())) / "offline_listener.stop"
    StorageService = autoclass("org.vosk.android.StorageService")
    Recognizer = autoclass("org.vosk.Recognizer")
    SpeechService = autoclass("org.vosk.android.SpeechService")
    ready = threading.Event()
    loaded_model: list[object] = []

    class ModelCallback(PythonJavaClass):
        __javainterfaces__ = ["org/vosk/android/StorageService$Callback"]
        __javacontext__ = "app"

        @java_method("(Lorg/vosk/Model;)V")
        def onComplete(self, model: object) -> None:
            loaded_model.append(model)
            ready.set()

        @java_method("(Ljava/lang/Exception;)V")
        def onError(self, error: object) -> None:
            LOG.error("Could not unpack the bundled Vosk model: %s", error)
            ready.set()

    callback = ModelCallback()
    StorageService.unpack(PythonService.mService, "model-en-us", "model-en-us", callback)
    if not ready.wait(45) or not loaded_model:
        LOG.error("Offline Vosk model did not become ready")
        return

    class RecognitionCallback(PythonJavaClass):
        __javainterfaces__ = ["org/vosk/android/RecognitionListener"]
        __javacontext__ = "app"

        @java_method("(Ljava/lang/String;)V")
        def onPartialResult(self, _hypothesis: str) -> None:
            pass

        @java_method("(Ljava/lang/String;)V")
        def onResult(self, hypothesis: str) -> None:
            transcript = _text_from_hypothesis(hypothesis)
            if transcript:
                _post_transcript(transcript)

        @java_method("(Ljava/lang/String;)V")
        def onFinalResult(self, hypothesis: str) -> None:
            transcript = _text_from_hypothesis(hypothesis)
            if transcript:
                _post_transcript(transcript)

        @java_method("(Ljava/lang/Exception;)V")
        def onError(self, error: object) -> None:
            LOG.error("Offline recognition stopped: %s", error)

        @java_method("()V")
        def onTimeout(self) -> None:
            LOG.info("Offline recognition timed out; Vosk will continue listening")

    stop_marker.unlink(missing_ok=True)

    recognizer = Recognizer(loaded_model[0], SAMPLE_RATE)
    listener = RecognitionCallback()
    speech = SpeechService(recognizer, SAMPLE_RATE)
    speech.startListening(listener)
    LOG.info("Offline Vosk listener started")
    try:
        while True:
            time.sleep(60)
    finally:
        speech.stop()
        recognizer.close()
        PythonService.mService.stopSelf()


if __name__ == "__main__":
    main()
