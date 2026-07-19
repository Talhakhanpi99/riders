"""Unified diagnostic output for adb logcat.

Filter on device:
    adb logcat | findstr "python"

Every message is prefixed with VOICERIDE so critical paths are easy to spot.
"""

from __future__ import annotations

import logging

_PREFIX = "VOICERIDE"


def log(
    tag: str,
    message: str,
    *args: object,
    level: int = logging.INFO,
    logger_name: str = "voiceride",
) -> None:
    text = f"{_PREFIX} [{tag}] {message % args}" if args else f"{_PREFIX} [{tag}] {message}"
    print(text, flush=True)
    logging.getLogger(logger_name).log(level, text)


def exception(tag: str, message: str, exc: BaseException, *, logger_name: str = "voiceride") -> None:
    text = f"{_PREFIX} [{tag}] {message}: {exc}"
    print(text, flush=True)
    logging.getLogger(logger_name).exception(text)
