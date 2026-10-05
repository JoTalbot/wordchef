# Word Chef v1.4.4

## Mobile game screen redesign

- Reworked the Android game composition so the cooking plate is the central visual stage.
- Letter circles now sit visibly over the dish instead of reading as a separate lower section.
- Rebalanced the level header, dish ribbon, guest/progress card, puzzle board, word selection bar and bottom actions for a 360–430px Android viewport.
- Added a distinct compact layout for narrow devices.
- Bumped Android versionCode from 5 to 6 so the new APK installs as a real upgrade instead of being mistaken for the old build.

## Verification

- Frontend build and typecheck are covered by CI.
- Android release APK is signed, zipaligned and checked for WebView assets.
- Backend release version is 1.4.4.
