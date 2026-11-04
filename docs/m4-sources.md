# M4b step 0 documentation fetches

Read-only fetches of official pages for the Assistant runtime/model
decision (1 Nov 2026). Step 0 budget: at most 20. Used: 20 (including
6 that returned HTTP 401; each counts toward the budget). Step 0b
budget: at most 8 new. Used: 8 (#21-28). Step 0c budget: at most 3
new. Used: 3 (#29-31). Step 0d: 1 metadata fetch (#32). Step 1C:
1 POM fetch (#33) + 6 granted metadata fetches (#34-39). Total: 39.

| # | URL | Date | Used for |
|---|---|---|---|
| 1 | https://github.com/pytorch/executorch/releases | 2026-11-01 | Latest ExecuTorch release: v1.5.1 (Latest tag) |
| 2 | https://docs.pytorch.org/executorch/stable/using-executorch-android.html | 2026-11-01 | Maven coordinates org.pytorch:executorch-android, prebuilt AAR per release (Maven + S3), arm64-v8a + x86_64, .pte format, Module.load(file path) |
| 3 | https://repo1.maven.org/maven2/org/pytorch/executorch-android/ | 2026-11-01 | Maven versions; latest 1.5.1 (2026-10-01) matches GitHub Latest |
| 4 | https://repo1.maven.org/maven2/org/pytorch/executorch-android/1.5.1/ | 2026-11-01 | AAR size: executorch-android-1.5.1.aar = 7584141 bytes |
| 5 | https://github.com/ggml-org/llama.cpp/releases | 2026-11-01 | Latest llama.cpp release: b12024 (pre-release, 31 Oct 2026); Android arm64 CPU asset listed |
| 6 | https://github.com/ggml-org/llama.cpp/blob/master/docs/android.md | 2026-11-01 | GGUF format, -m file path, AiChat app-private file path, cross-compile -DANDROID_PLATFORM=android-28 |
| 7 | https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct | 2026-11-01 | Licence llama3.2, Llama 3.2 Community Licence text ("I Accept"), safetensors tag |
| 8 | https://huggingface.co/api/models/meta-llama/Llama-3.2-1B-Instruct/tree/main | 2026-11-01 | model.safetensors = 2471645608 bytes (exceeds 2 GB); tokenizer.json = 9085657 bytes |
| 9 | https://huggingface.co/ggml-org/Llama-3.2-1B-Instruct-Q8_0-GGUF | 2026-11-01 | HTTP 401 (gated/private/unlisted; counts toward budget) |
| 10 | https://huggingface.co/api/models/ggml-org/Llama-3.2-1B-Instruct-Q8_0-GGUF/tree/main | 2026-11-01 | HTTP 401 (same) |
| 11 | https://huggingface.co/api/models/bartowski/Llama-3.2-1B-Instruct-GGUF/tree/main | 2026-11-01 | Ungated GGUF sizes: Q4_K_M = 807694464, IQ3_M = 657289344, others; revision main |
| 12 | https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF | 2026-11-01 | Licence llama3.2, GGUF library tag, ungated (200 OK), llama.cpp usage |
| 13 | https://huggingface.co/api/models/ggml-org/SmolLM2-135M-Instruct-Q8_0-GGUF/tree/main | 2026-11-01 | HTTP 401 (no usable entry; counts toward budget) |
| 14 | https://huggingface.co/api/models/HuggingFaceTB/SmolLM2-135M-Instruct-GGUF/tree/main | 2026-11-01 | HTTP 401 (no usable entry; counts toward budget) |
| 15 | https://huggingface.co/api/models/bartowski/SmolLM2-135M-Instruct-GGUF/tree/main | 2026-11-01 | Ungated GGUF sizes: Q4_K_M = 105454432, Q2_K = 88202080, others; revision main |
| 16 | https://github.com/ggml-org/llama.cpp/blob/master/LICENSE | 2026-11-01 | llama.cpp licence: MIT |
| 17 | https://github.com/ggml-org/llama.cpp/releases/expanded_assets/b12024 | 2026-11-01 | Android arm64 tar.gz = 75.3 MB as listed, sha256 6b753a60abae6c58c38de49d26b60c2a80fbff530f2827704283137c5f8cdb0b |
| 18 | https://github.com/pytorch/executorch/blob/main/LICENSE | 2026-11-01 | ExecuTorch licence: BSD License (Copyright Meta Platforms, Inc. and affiliates) |
| 19 | https://huggingface.co/meta-llama/Muse-Glimmer | 2026-11-01 | HTTP 401 (no usable info; Q15 stays unknown; counts toward budget) |
| 20 | https://huggingface.co/api/models/meta-llama/Muse-Glimmer/tree/main | 2026-11-01 | HTTP 401 (same) |
| 21 | https://huggingface.co/api/models/bartowski/Llama-3.2-1B-Instruct-GGUF/revision/main | 2026-11-01 | Pinned commit SHA 067b946cf014b7c697f3654f621d577a3e3afd1c; author bartowski, base meta-llama, licence llama3.2, ungated |
| 22 | https://huggingface.co/api/models/bartowski/Llama-3.2-1B-Instruct-GGUF/tree/067b946cf014b7c697f3654f621d577a3e3afd1c | 2026-11-01 | LFS SHA-256 + bytes at pin: Q4_K_M = 807694464 / 6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83; IQ3_M = 657289344 / c80cc062a721c267ec50fee83fe6b55d36fc7abe708392dbe22e16fbd42687e8 |
| 23 | https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=30&page=1 | 2026-11-01 | 30 newest releases (29-31 Oct 2026); all newer than the 14-day cutoff, none usable as the settled build |
| 24 | https://huggingface.co/api/models?author=executorch-community&search=llama&limit=100 | 2026-11-01 | 6 models; Llama 3.2 1B Instruct .pte candidates (ET, SpinQuant_INT4_EO8-ET, QLORA_INT4_EO8-ET), all licence:llama3.2, public; list carries no commit SHA |
| 25 | https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=100&page=2 | 2026-11-01 | Releases 101-200 (19-25 Oct 2026); still newer than the cutoff |
| 26 | https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=100&page=3 | 2026-11-01 | Releases 201-300; settled pick b11721 (18 Oct 2026, commit ff5a2337), android-arm64 tarball = 76297099 bytes; notes list no asset contents |
| 27 | https://github.com/ggml-org/llama.cpp/releases/expanded_assets/b11721 | 2026-11-01 | llama-b11721-bin-android-arm64.tar.gz = 72.8 MB listed, sha256 94502b60b23f1cbc651e7d9720dacdc5b974686f9a61cf337d0611c1c1b9850f |
| 28 | https://huggingface.co/api/models/executorch-community/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8-ET/revision/main | 2026-11-01 | .pte repo pinned at 911221e0f49d3354cbd4697a9c09feee25cadec7; licence llama3.2, ungated; files include .pte + tokenizer.model (bytes/SHA-256 need one more tree fetch) |
| 29 | https://huggingface.co/api/models/executorch-community/Llama-3.2-1B-Instruct-SpinQuant_INT4_EO8-ET/tree/911221e0f49d3354cbd4697a9c09feee25cadec7 | 2026-11-01 | .pte = 1135951488 / 8715cdba9e91f6bede00cc5f2d6b12397b95225da5630c4972a8da03001cda3b; tokenizer.model = 2183982 / 82e9d31979e92ab929cd544440f129d9ecd797b69e327f80f17e1c50d5551b55; small files bytes only |
| 30 | https://raw.githubusercontent.com/pytorch/executorch/v1.5.1/extension/android/build.gradle | 2026-11-01 | 200 but parent buildscript only (repos + AGP classpath); no minSdk, no namespace |
| 31 | https://raw.githubusercontent.com/pytorch/executorch/v1.5.1/extension/android/executorch-android/build.gradle | 2026-11-01 | HTTP 404 (wrong module path; budget exhausted, minSdk still unknown) |
| 32 | https://repo1.maven.org/maven2/org/pytorch/executorch-android/1.5.1/executorch-android-1.5.1.aar.sha256 | 2026-11-01 | AAR SHA-256 published: d67d39eb51e00c996162e1d9898519a622918b20e4e18ea479f5fee4fe920e72 |
| 33 | https://repo1.maven.org/maven2/org/pytorch/executorch-android/1.5.1/executorch-android-1.5.1.pom | 2026-11-01 | Runtime deps: kotlin-stdlib 1.9.23, fbjni 0.7.0, nativeloader 0.10.5, core-ktx 1.13.1 |
| 34 | https://repo1.maven.org/maven2/org/jetbrains/kotlin/kotlin-stdlib/1.9.23/ | 2026-11-01 | kotlin-stdlib-1.9.23.jar = 1718956 bytes; no .sha256 row (only .md5/.sha1) |
| 35 | https://repo1.maven.org/maven2/org/jetbrains/kotlin/kotlin-stdlib/1.9.23/kotlin-stdlib-1.9.23.jar.sha256 | 2026-11-01 | HTTP 404 (no published SHA-256; verified after download) |
| 36 | https://repo1.maven.org/maven2/com/facebook/fbjni/fbjni/0.7.0/ | 2026-11-01 | fbjni-0.7.0.aar = 4349156 bytes; .sha256 published |
| 37 | https://repo1.maven.org/maven2/com/facebook/fbjni/fbjni/0.7.0/fbjni-0.7.0.aar.sha256 | 2026-11-01 | AAR SHA-256: 7e319ae110ac5e5ef18904170aea5c3e753e915d196699d7fd39d36c8e1dfe36 |
| 38 | https://repo1.maven.org/maven2/com/facebook/soloader/nativeloader/0.10.5/ | 2026-11-01 | nativeloader-0.10.5.jar = 3158 bytes; .sha256 published |
| 39 | https://repo1.maven.org/maven2/com/facebook/soloader/nativeloader/0.10.5/nativeloader-0.10.5.jar.sha256 | 2026-11-01 | JAR SHA-256: 971ded86009c0ac56fb2275e128f852e7dcbc41a40cd799688f8a1aebde5195d |

Notes:

- Fetches 9, 10, 13, 14, 19, 20 returned HTTP 401. Hugging Face
  returns 401 for gated, private, or unlisted repos when fetched
  without credentials, so a 401 proves only "no public metadata",
  not existence or gating specifically.
- No Meta or Baseten API calls were made. No model, library, or
  tool was downloaded. No installs or builds.
- Step 0b (1 Nov 2026): 8/8 new fetches used. The 8 did not stretch
  to a `.pte` tree fetch (bytes/SHA-256 of the ExecuTorch files) or
  to the Gradle source for ExecuTorch's minimum Android version;
  both are named as follow-ups in `docs/m4-assistant.md`.
- Step 0c (1 Nov 2026): 3/3 new fetches used. #29 recorded the `.pte`
  + tokenizer bytes and SHA-256. #30-31 tried the Android Gradle
  source at tag v1.5.1 for minSdk without success (parent file has
  no minSdk; guessed module path 404). Next step: list
  `extension/android` at v1.5.1 to find the module directory, then
  fetch its Gradle file (up to 2 fetches).
- Step 0d (1 Nov 2026): owner-approved downloads of both pairs
  (5 files, 2029711174 bytes; every file's bytes and SHA-256 matched
  the published values, so nothing was deleted). minSdk (23) and the
  LLM class were read offline from the verified AAR; the tarball was
  listed, nothing run. Downloads are not fetches; only #32 counts.
- Step 1C (1 Nov 2026): owner granted 6 metadata fetches (#34-39) after
  the POM (#33). Resolved: kotlin-stdlib JAR bytes (no published
  SHA-256; computed after download), fbjni AAR bytes + hash,
  nativeloader JAR bytes + hash. core-ktx excluded: zero androidx
  references in the AAR's 60 classes (offline scan, no fetch).
- Spec: plan/milestones/M4-image-build.md at 128e062535359808a89466cdc42df632359d4b99
  (merged commit; read via git show after fetch; checkout untouched).
- M3 base: catalog/models.json, docs/catalog.md, docs/fit-budgets.md
  at e701acd in this worktree.
