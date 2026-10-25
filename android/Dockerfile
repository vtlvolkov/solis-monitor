# Builds the TV app without an Android SDK on the host: `sh build.sh` -> dist/solis-tv.apk.
FROM gradle:8.9-jdk17
USER root
ENV HOME=/root GRADLE_USER_HOME=/root/.gradle ANDROID_HOME=/opt/android-sdk ANDROID_SDK_ROOT=/opt/android-sdk
RUN apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/*
RUN mkdir -p $ANDROID_HOME/cmdline-tools \
 && curl -fsSL -o /tmp/tools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip \
 && unzip -q /tmp/tools.zip -d $ANDROID_HOME/cmdline-tools \
 && mv $ANDROID_HOME/cmdline-tools/cmdline-tools $ANDROID_HOME/cmdline-tools/latest \
 && rm /tmp/tools.zip
ENV PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin
RUN yes | sdkmanager --licenses > /dev/null \
 && sdkmanager "platforms;android-34" "build-tools;34.0.0"

WORKDIR /src
COPY .keystore/debug.keystore /root/.android/debug.keystore
COPY settings.gradle.kts build.gradle.kts gradle.properties ./
COPY app app
# A failing unit test fails the build, like the other Dockerfiles in this repo.
ARG APP_VERSION=1.0
ARG APP_VERSION_CODE=1
RUN gradle --no-daemon --console=plain -PappVersion=$APP_VERSION -PappVersionCode=$APP_VERSION_CODE testDebugUnitTest assembleRelease
