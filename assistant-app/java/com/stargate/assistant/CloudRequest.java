package com.stargate.assistant;

import java.nio.charset.Charset;

/**
 * Cloud request builder (pure Java). Each request carries one
 * provider's key to that provider's own host; the key comes from
 * the caller's slot for that provider, never from a shared
 * variable. Enforces the typed Meta model ID format, the fixed
 * open-weight ID and the input caps before anything is sent.
 */
public final class CloudRequest {

    /** One request ready for the network layer. */
    public static final class WireRequest {
        public final String provider;
        public final String host;
        public final String path;
        public final String authHeader;
        public final String body;
        public final String model;

        private WireRequest(String provider, String host, String path,
                String authHeader, String body, String model) {
            this.provider = provider;
            this.host = host;
            this.path = path;
            this.authHeader = authHeader;
            this.body = body;
            this.model = model;
        }
    }

    /** Refusal before sending: the fake network layer never sees it. */
    public static final class Refused extends Exception {
        public final String code;

        Refused(String code, String message) {
            super(message);
            this.code = code;
        }
    }

    private static final Charset UTF8 = Charset.forName("UTF-8");

    private CloudRequest() {
    }

    /** UTF-8 byte length of a string. */
    public static int utf8Bytes(String text) {
        return (text == null ? "" : text).getBytes(UTF8).length;
    }

    /** Unicode code points in a string. */
    public static int codePoints(String text) {
        if (text == null) {
            return 0;
        }
        return text.codePointCount(0, text.length());
    }

    /**
     * Refuses a question over 2,000 code points or 8,000 UTF-8
     * bytes. Accepted questions are returned unchanged.
     */
    public static String checkQuestion(String question)
            throws Refused {
        String asked = question == null ? "" : question;
        if (codePoints(asked) > CloudSpec.QUESTION_MAX_CHARS) {
            throw new Refused("question",
                    "question too long to send");
        }
        if (utf8Bytes(asked) > CloudSpec.QUESTION_MAX_BYTES) {
            throw new Refused("question",
                    "question too long to send");
        }
        return asked;
    }

    /**
     * Typed Meta IDs are non-empty, at most 100 characters, and
     * use only letters, digits, dot, dash, underscore and slash;
     * the open-weight ID is fixed exactly.
     */
    public static void checkModel(String provider, String model)
            throws Refused {
        if (CloudSpec.META.equals(provider)) {
            if (model == null || model.isEmpty()
                    || model.length()
                            > CloudSpec.MODEL_ID_MAX_LEN) {
                throw new Refused("model",
                        "model not available");
            }
            for (int i = 0; i < model.length(); i++) {
                char c = model.charAt(i);
                boolean ok = (c >= 'A' && c <= 'Z')
                        || (c >= 'a' && c <= 'z')
                        || (c >= '0' && c <= '9')
                        || c == '.' || c == '-'
                        || c == '_' || c == '/';
                if (!ok) {
                    throw new Refused("model",
                            "model not available");
                }
            }
            return;
        }
        if (CloudSpec.BASETEN.equals(provider)) {
            if (!CloudSpec.OPEN_WEIGHT_MODEL.equals(model)) {
                throw new Refused("model",
                        "model not available");
            }
            return;
        }
        throw new Refused("provider", "unknown provider");
    }

    /**
     * Builds one request. Over-limit questions, bad model IDs,
     * output caps above 4,096 and bodies over 16 KB are refused
     * before the network layer sees anything.
     */
    public static WireRequest build(String provider, String key,
            String model, String system, String question,
            int maxTokens) throws Refused {
        if (!CloudSpec.META.equals(provider)
                && !CloudSpec.BASETEN.equals(provider)) {
            throw new Refused("provider", "unknown provider");
        }
        if (key == null || key.isEmpty()) {
            throw new Refused("no-key", "no key saved");
        }
        checkModel(provider, model);
        String asked = checkQuestion(question);
        if (maxTokens < 1
                || maxTokens > CloudSpec.OUTPUT_MAX_TOKENS) {
            throw new Refused("output", "output cap exceeded");
        }
        String body = Json.chatBody(model, system, asked,
                CloudSpec.tokenField(provider), maxTokens);
        if (utf8Bytes(body) > CloudSpec.BODY_MAX_BYTES) {
            throw new Refused("body", "request body over 16 KB");
        }
        return new WireRequest(provider, CloudSpec.host(provider),
                CloudSpec.path(provider), "Bearer " + key, body,
                model);
    }
}
