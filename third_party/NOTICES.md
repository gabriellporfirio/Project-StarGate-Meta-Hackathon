# Third-party notices (StarGate Assistant)

Everything below either ends up inside `com.stargate.assistant`'s APK
or is shipped beside it (model files). Licence names and copyright
lines are read from the downloaded files only (Maven POM licence
block, or LICENSE/NOTICE inside the AAR/JAR). Where the files say
nothing, the entry reads "unknown, check before release" rather than
a guess. Same text ships in the APK as `assets/licenses.txt`.
(1 Nov 2026.)

## In the APK

### ExecuTorch Android 1.5.1

- Version: 1.5.1. Source: Maven Central
  `org.pytorch:executorch-android:1.5.1`
  (`executorch-android-1.5.1.aar`, 7584141 bytes).
- Licence: BSD 3-Clause, read from the `<licenses>` block (line 20)
  of `/workspaces/m4b-tools/executorch-android-1.5.1.pom` (copy of
  fetch #33, saved beside the AAR; no licence file inside the AAR).
- Copyright: unknown, check before release (no copyright line in the
  POM or the AAR).

### fbjni 0.7.0

- Version: 0.7.0. Source: Maven Central
  `com.facebook.fbjni:fbjni:0.7.0` (`fbjni-0.7.0.aar`, 4349156 bytes).
- Licence: Apache License, Version 2.0, read from the file headers
  inside the AAR, e.g.
  `prefab/modules/fbjni/include/fbjni/fbjni.h:1-14` ("Licensed
  under the Apache License, Version 2.0"; same text in all 36 headers,
  spot-checked `detail/Hybrid.h` and `lyra/lyra.h`). No
  LICENSE/NOTICE filename in the AAR, and the headers themselves
  do not ship in the APK; they are evidence only.
- Copyright: Copyright (c) Facebook, Inc. and its affiliates.
  (same headers, e.g. `fbjni.h:2`).
- Bundled inside this AAR and shipped in the APK:
  `libc++_shared.so` (native C++ runtime, per ABI). Licence:
  unknown, check before release (its strings show only
  llvm-project paths, no licence text).

### SoLoader nativeloader 0.10.5

- Version: 0.10.5. Source: Maven Central
  `com.facebook.soloader:nativeloader:0.10.5`
  (`nativeloader-0.10.5.jar`, 3158 bytes).
- Licence: unknown, check before release (no POM on file, no
  LICENSE/NOTICE inside the JAR).
- Copyright: unknown, check before release.

### Kotlin standard library 1.9.23

- Version: 1.9.23. Source: Maven Central
  `org.jetbrains.kotlin:kotlin-stdlib:1.9.23`
  (`kotlin-stdlib-1.9.23.jar`, 1718956 bytes).
- Licence: unknown, check before release (no POM on file, no
  LICENSE/NOTICE inside the JAR).
- Copyright: unknown, check before release.

## Shipped beside the APK (never in it)

### Llama 3.2 1B Instruct SpinQuant INT4 (ExecuTorch export)

- Files: `Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8.pte`
  (1135951488 bytes) + `tokenizer.model` (2183982 bytes).
- Source: `executorch-community/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8-ET`
  at `911221e0f49d3354cbd4697a9c09feee25cadec7`.
- Licence: Llama 3.2 Community License.
- Attribution (exactly as recorded in `docs/m4-assistant.md`; keep
  with distributed copies):
  "Llama 3.2 is licensed under the Llama 3.2 Community License,
  Copyright (c) Meta Platforms, Inc. All Rights Reserved."
