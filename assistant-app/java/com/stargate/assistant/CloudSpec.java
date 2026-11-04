package com.stargate.assistant;

/**
 * Pinned cloud provider values (M4c spec section 3) and caps
 * (section 6). Pure Java, so the host test runs the request,
 * error and gate logic built on these exact constants on a JVM.
 * v2.5: users bring their own key, no money tracking; only
 * per-request limits stay.
 */
public final class CloudSpec {

    public static final String META = "meta";
    public static final String BASETEN = "baseten";

    public static final String META_DISPLAY = "Meta";
    public static final String BASETEN_DISPLAY = "Baseten";

    public static final String META_HOST = "api.meta.ai";
    public static final String META_PATH = "/v1/chat/completions";
    public static final String BASETEN_HOST = "inference.baseten.co";
    public static final String BASETEN_PATH = "/v1/chat/completions";

    public static final String META_TOKEN_FIELD = "max_completion_tokens";
    public static final String BASETEN_TOKEN_FIELD = "max_tokens";

    public static final String DEFAULT_META_MODEL = "muse-spark-1.3";
    public static final String OPEN_WEIGHT_MODEL =
            "meta-models/muse-spark-open-1026";

    public static final String MODE_LOCAL = "Local";
    public static final String MODE_META = "Meta";
    public static final String MODE_OPEN = "Meta open-weight";

    /** Input caps: one question, both apply. */
    public static final int QUESTION_MAX_CHARS = 2000;
    public static final int QUESTION_MAX_BYTES = 8000;
    /** Whole request body cap. */
    public static final int BODY_MAX_BYTES = 16384;
    /** Output cap sent in the request token field. */
    public static final int OUTPUT_MAX_TOKENS = 4096;
    /** Reply bodies past this are cut off and labelled. */
    public static final int REPLY_MAX_BYTES = 262144;
    /** One deadline for the request including its one retry. */
    public static final long TIMEOUT_MS = 60000L;
    /** A 429 wait longer than this means no retry. */
    public static final int RETRY_AFTER_MAX_S = 10;
    /** Default 429 wait when the provider names none. */
    public static final int RETRY_AFTER_DEFAULT_S = 1;
    /** Longest typed Meta model ID. */
    public static final int MODEL_ID_MAX_LEN = 100;

    /** Label for a reply cut at {@link #REPLY_MAX_BYTES}. */
    public static final String TRUNCATED_LABEL =
            "\n[truncated: reply exceeded 256 KB]";
    /** Label for a reply whose finish_reason is not stop. */
    public static final String INCOMPLETE_LABEL = " [incomplete]";

    /** On-screen blurbs (spec section 1, exact text). */
    public static final String LOCAL_BLURB =
            "Picked to fit your phone. Works offline.";
    public static final String CLOUD_BLURB =
            "Uses your own API key. Your provider's terms and "
            + "usage limits apply.";

    /** Provider terms pages, fixed in the app (owner checks). */
    public static final String META_TERMS_URL =
            "https://dev.meta.ai/legal/terms-of-service";
    public static final String BASETEN_TERMS_URL =
            "https://www.baseten.co/terms-and-conditions/";

    private CloudSpec() {
    }

    public static String host(String provider) {
        if (META.equals(provider)) {
            return META_HOST;
        }
        if (BASETEN.equals(provider)) {
            return BASETEN_HOST;
        }
        throw new IllegalArgumentException("unknown provider");
    }

    public static String path(String provider) {
        if (META.equals(provider)) {
            return META_PATH;
        }
        if (BASETEN.equals(provider)) {
            return BASETEN_PATH;
        }
        throw new IllegalArgumentException("unknown provider");
    }

    public static String tokenField(String provider) {
        if (META.equals(provider)) {
            return META_TOKEN_FIELD;
        }
        if (BASETEN.equals(provider)) {
            return BASETEN_TOKEN_FIELD;
        }
        throw new IllegalArgumentException("unknown provider");
    }

    public static String display(String provider) {
        if (META.equals(provider)) {
            return META_DISPLAY;
        }
        if (BASETEN.equals(provider)) {
            return BASETEN_DISPLAY;
        }
        throw new IllegalArgumentException("unknown provider");
    }

    public static String termsUrl(String provider) {
        if (META.equals(provider)) {
            return META_TERMS_URL;
        }
        if (BASETEN.equals(provider)) {
            return BASETEN_TERMS_URL;
        }
        throw new IllegalArgumentException("unknown provider");
    }
}
