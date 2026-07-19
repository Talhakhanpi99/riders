"""Unpack bundled Vosk model assets on Android without StorageService."""

from __future__ import annotations

import os
from typing import Any

from diag_log import log


def vosk_java_available() -> tuple[bool, str | None]:
    """Return whether org.vosk.Model is present in the APK classpath."""
    try:
        from jnius import autoclass  # type: ignore

        autoclass("org.vosk.Model")
        return True, None
    except Exception as exc:
        return False, str(exc)


def _stream_to_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    return bytes(bytearray(data))


def _copy_asset_tree(assets: Any, asset_path: str, dest_path: str) -> None:
    items = assets.list(asset_path)
    if not items:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        stream = assets.open(asset_path)
        try:
            with open(dest_path, "wb") as handle:
                handle.write(_stream_to_bytes(stream.read()))
        finally:
            stream.close()
        return

    os.makedirs(dest_path, exist_ok=True)
    for name in items:
        if not name:
            continue
        child_asset = f"{asset_path}/{name}"
        _copy_asset_tree(assets, child_asset, os.path.join(dest_path, name))


def unpack_model_from_assets(context: Any, asset_name: str, dest_name: str) -> str:
    """Copy a bundled asset folder into the app files directory."""
    files_dir = str(context.getFilesDir().getAbsolutePath())
    dest_path = os.path.join(files_dir, dest_name)
    if os.path.isdir(dest_path) and os.listdir(dest_path):
        log("VOSK", "Model directory already present at %s", dest_path)
        return dest_path

    log("VOSK", "Copying bundled assets %r -> %s", asset_name, dest_path)
    _copy_asset_tree(context.getAssets(), asset_name, dest_path)
    if not os.path.isdir(dest_path) or not os.listdir(dest_path):
        raise RuntimeError(f"Asset unpack produced an empty directory at {dest_path}")
    log("VOSK", "Asset unpack complete | files=%d", len(os.listdir(dest_path)))
    return dest_path
