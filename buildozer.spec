[app]
title = VoiceRide
# (bool) Indicate if you want to automatically accept SDK license
android.accept_sdk_license = True
package.name = voiceride
package.domain = com.voiceride
source.dir = .
source.include_exts = py,html,css,js,png,json,md,db
source.exclude_dirs = tests,.git,.github,.venv,venv,venv-broken,__pycache__,bin,.pytest_cache,.ruff_cache,ai,android,app,config,database,models,permissions,services,utils,docs,logs
version = 0.1.1
requirements = python3,kivy,android,flask,werkzeug,rapidfuzz,python-dotenv,pyjnius
orientation = portrait
fullscreen = 0
# Calls and SMS are requested only when the rider invokes those features.
android.permissions = RECORD_AUDIO,POST_NOTIFICATIONS,INTERNET,ACCESS_FINE_LOCATION,ACCESS_COARSE_LOCATION,FOREGROUND_SERVICE,FOREGROUND_SERVICE_MICROPHONE,FOREGROUND_SERVICE_LOCATION,FOREGROUND_SERVICE_CAMERA,FOREGROUND_SERVICE_CONNECTED_DEVICE,CAMERA,READ_CONTACTS,CALL_PHONE,SEND_SMS,READ_SMS,VIBRATE,MODIFY_AUDIO_SETTINGS,BLUETOOTH,BLUETOOTH_CONNECT,CHANGE_WIFI_STATE,ACCESS_WIFI_STATE
android.api = 34
android.build_tools_version = 34.0.0
# Python 3.14 remote debugging uses Android APIs exposed from NDK API 24.
android.minapi = 24
android.ndk_api = 24
android.ndk = 25b
android.archs = arm64-v8a
android.allow_backup = False
android.private_storage = True
# Required because the Android WebView loads the local Flask server over http://127.0.0.1.
# This is inserted as attributes of AndroidManifest.xml's <application> element.
android.extra_manifest_application_arguments = android/manifest_application_arguments.xml
# android.gradle_dependencies =
android.enable_androidx = True
# Foreground service removed from the simplified source layout. The main app
# still runs as a normal Kivy/Flask/WebView APK.
# services =
[buildozer]
log_level = 2
warn_on_root = 1

