#!/usr/bin/env bash
# Build the Word Chef Android APK (WebView shell + static export).
# Requirements: JDK 17, Android SDK (ANDROID_HOME), Gradle 8.x on PATH or $GRADLE.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "1/5 · web export"
(cd frontend && npm install --no-audit --no-fund && npm run build)

echo "2/5 · sync assets"
rm -rf android/app/src/main/assets/www
mkdir -p android/app/src/main/assets
cp -r frontend/out android/app/src/main/assets/www
# AAPT skips asset dirs starting with "_" — Next.js emits "_next", so rename it
if [ -d android/app/src/main/assets/www/_next ]; then
  mv android/app/src/main/assets/www/_next android/app/src/main/assets/www/next
fi
python3 - << 'PYEOF'
import pathlib, re
root = pathlib.Path("android/app/src/main/assets/www")
n = 0
for f in root.rglob("*"):
    if f.is_file() and f.suffix in {".html", ".js", ".css", ".txt", ".json"}:
        s = f.read_text(encoding="utf-8")
        orig = s
        # loop: "__next"-style identifiers shed one underscore per pass
        while "_next" in s:
            s = s.replace("_next", "next")
        if f.suffix != ".css":
            # in HTML/JS a leading "/" is document-relative → make it "./"
            s = s.replace('"/next/', '"./next/').replace("(/next/", "(./next/")
            s = re.sub(r'(href|src)="/(icon\.png|img/)', r'\1="./\2', s)
        if s != orig:
            f.write_text(s, encoding="utf-8")
            n += 1

# CSS is different: url(/…) resolves against the *stylesheet*, not the
# document. Rewrite each absolute url() to the right number of "../" for the
# file's own depth (the game CSS lives at next/static/css/, i.e. three up).
for f in root.rglob("*.css"):
    depth = len(f.relative_to(root).parts) - 1
    prefix = "../" * depth
    s = f.read_text(encoding="utf-8")
    new = re.sub(r"url\((['\"]?)/(?!/)", lambda m: f"url({m.group(1)}{prefix}", s)
    if new != s:
        f.write_text(new, encoding="utf-8")
        n += 1
        print(f"css urls → relative: {f.relative_to(root)} ({prefix or './'})")
print(f"rewrote bundle paths in {n} files")
PYEOF

echo "3/5 · keystore"
if [ ! -f android/keystore.jks ]; then
  keytool -genkeypair -v -keystore android/keystore.jks -alias wordchef \
    -keyalg RSA -keysize 2048 -validity 10000 \
    -storepass "${WC_KEYSTORE_PASS:-wordchef}" \
    -keypass "${WC_KEY_PASS:-wordchef}" \
    -dname "CN=Word Chef, O=WordChef, C=UA"
fi

echo "4/5 · gradle assembleRelease"
GRADLE_BIN="${GRADLE:-gradle}"
(cd android && "$GRADLE_BIN" --no-daemon assembleRelease)

echo "5/5 · apk"
ls -lh android/app/build/outputs/apk/release/*.apk
