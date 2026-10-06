# Zetic MLange Android

This repository distributes public Android artifacts for the Zetic MLange SDK.
Each SDK version is published as a GitHub Release. Release assets use a flat
layout, so Android and Flutter consumers resolve them through a Gradle Ivy
pattern repository and Gradle module metadata.

## Install

Add this repository to the consuming Android application's repository list.
Keep `google()` and `mavenCentral()` because MLange has public Android and
Kotlin dependencies outside this release.

```kotlin
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.PREFER_SETTINGS)
    repositories {
        google()
        mavenCentral()
        ivy {
            name = "ZeticMLangeGitHub"
            url = uri("https://github.com/zetic-ai/ZeticMLangeAndroid/releases/download")
            patternLayout {
                artifact("[revision]/[artifact]-[revision](-[classifier]).[ext]")
            }
            metadataSources {
                gradleMetadata()
            }
            content {
                includeGroup("com.zeticai.mlange")
                includeGroup("com.zeticai.mlange.backend")
            }
        }
    }
}
```

Then declare the SDK as usual:

```kotlin
dependencies {
    implementation("com.zeticai.mlange:mlange:1.11.0")
}
```

The tag and version must match. For example, Gradle resolves the aggregate
metadata for `1.11.0` from
`releases/download/1.11.0/mlange-1.11.0.module` and follows its transitive
dependencies from the same release.

## Integrity and provenance

Every release includes `release-manifest.json` and `SHA256SUMS`. The manifest
records the source SDK commit, build infrastructure commit, native dependency
pin, source bundle digest, and SHA-256 digest for every published asset.

Binary artifacts are release assets only; they are never committed to this Git
repository. Use `scripts/prepare_release_assets.py` to derive a release asset
set and its manifest from a verified Android publication bundle.
