# Vosk Android libraries

Buildozer needs the Vosk Android AAR and native library on disk before building the APK.

From the repo root:

```bash
bash scripts/fetch_vosk_android.sh
```

This downloads `vosk-android-0.3.32.aar` from Maven Central and extracts `libvosk.so` for `arm64-v8a`.

GitHub Actions runs the same script automatically before `buildozer android debug`.
