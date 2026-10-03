# Word Chef v1.4.1

## Android WebView asset fix

- Android WebView now serves the packaged Next.js export through AndroidX WebViewAssetLoader.
- Root-relative Next.js `/_next/` and `/img/` resources are mapped to packaged APK assets.
- The app loads the packaged frontend through the local HTTPS asset origin.
- File and content URL access remain disabled.
- Frontend, backend and Android application metadata are aligned to v1.4.1.

## Verification

- Frontend production export: PASS
- Android signed release APK build: PASS
- APK alignment and signature verification: PASS
- Packaged WebView asset verification: PASS
- Backend/game test battery: rerun after version assertion update

## Compatibility

- Android 8.0+ (minSdk 26)
- Server-authoritative gameplay protocol unchanged
- No database migration
