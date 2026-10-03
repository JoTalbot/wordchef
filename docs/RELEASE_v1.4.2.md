# Word Chef v1.4.2

## Android release pipeline correction

- Android application metadata is aligned to v1.4.2.
- Frontend and backend runtime metadata are aligned to v1.4.2.
- Release pipeline must publish the APK as `wordchef-v1.4.2.apk`.
- Workflow artifact must be named `wordchef-v1.4.2-apk`.
- APK upload must target the `v1.4.2` GitHub Release.
- Android WebViewAssetLoader fix from v1.4.1 remains included.

## Verification

- Frontend production export
- Android signed release APK
- APK alignment and signature verification
- Packaged WebView asset verification
- Backend/game tests
- Prolepsis acceptance

## Compatibility

- Android 8.0+ (minSdk 26)
- Server-authoritative gameplay protocol unchanged
- No database migration
