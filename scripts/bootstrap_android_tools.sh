#!/usr/bin/env bash
# Download JDK 17 + Gradle 8.7 + Android SDK 34 into $TOOLS_DIR (default ~/tools).
# Idempotent: skips work when components are intact (snapshots may gut them).
set -euo pipefail
TOOLS_DIR="${TOOLS_DIR:-$HOME/tools}"
mkdir -p "$TOOLS_DIR"
cd "$TOOLS_DIR"

if ! ./jdk-17*/bin/java -version 2>/dev/null | grep -q '"17'; then
  rm -rf jdk-17*
  curl -sL -o jdk17.tar.gz "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse?project=jdk"
  tar xzf jdk17.tar.gz && rm jdk17.tar.gz
fi
export JAVA_HOME=$(ls -d "$TOOLS_DIR"/jdk-17*)

if [ ! -x gradle-8.7/bin/gradle ] || ! gradle-8.7/bin/gradle --version 2>/dev/null | grep -q "Gradle 8.7"; then
  rm -rf gradle-8.7
  curl -sL -o gradle.zip "https://services.gradle.org/distributions/gradle-8.7-bin.zip"
  unzip -q gradle.zip && rm gradle.zip && chmod +x gradle-8.7/bin/*
fi

export ANDROID_HOME="$TOOLS_DIR/android-sdk"
if [ ! -f "$ANDROID_HOME/platforms/android-34/android.jar" ] || [ ! -f "$ANDROID_HOME/build-tools/34.0.0/aapt2" ]; then
  if [ ! -f "$ANDROID_HOME/cmdline-tools/latest/lib/sdkmanager-classpath.jar" ] && [ ! -f "$ANDROID_HOME/cmdline-tools/latest/lib/sdk-manager.jar" ]; then
    rm -rf "$ANDROID_HOME/cmdline-tools"
  fi
  if [ ! -f "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" ]; then
    curl -sL -o cmdtools.zip "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
    mkdir -p android-sdk/cmdline-tools
    unzip -q cmdtools.zip -d android-sdk/cmdline-tools
    mv android-sdk/cmdline-tools/cmdline-tools android-sdk/cmdline-tools/latest
    rm cmdtools.zip
  fi
  chmod +x "$ANDROID_HOME"/cmdline-tools/latest/bin/* 2>/dev/null || true
  export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
  yes | sdkmanager --licenses >/dev/null 2>&1 || true
  sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0" >/dev/null
fi
echo "tools ready: JAVA_HOME=$JAVA_HOME"
