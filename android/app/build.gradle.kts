plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.solismonitor.tv"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.solismonitor.tv"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Sideloaded personal app: sign with the keystore that android/build.sh keeps in
            // android/.keystore, so every build has the same signature and installs as an update.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    lint { checkReleaseBuilds = false }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}
