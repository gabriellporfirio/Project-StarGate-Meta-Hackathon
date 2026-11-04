package com.stargate.assistant;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON for the cloud providers (pure Java). Builds the chat
 * request body with correct string escaping, and parses the fixed
 * reply shape both providers share: the first choice's message
 * text, its finish_reason, and usage counts.
 */
public final class Json {

    /** Parsed chat reply. {@link #ok} is false when malformed. */
    public static final class Reply {
        public final boolean ok;
        public final String text;
        public final String finishReason;
        public final boolean hasUsage;
        public final long promptTokens;
        public final long completionTokens;

        private Reply(boolean ok, String text, String finishReason,
                boolean hasUsage, long promptTokens,
                long completionTokens) {
            this.ok = ok;
            this.text = text;
            this.finishReason = finishReason;
            this.hasUsage = hasUsage;
            this.promptTokens = promptTokens;
            this.completionTokens = completionTokens;
        }
    }

    private Json() {
    }

    /** One JSON string literal with quotes, backslashes and controls. */
    public static String quote(String raw) {
        StringBuilder out = new StringBuilder(raw.length() + 2);
        out.append('"');
        for (int i = 0; i < raw.length(); i++) {
            char c = raw.charAt(i);
            switch (c) {
                case '"':
                    out.append("\\\"");
                    break;
                case '\\':
                    out.append("\\\\");
                    break;
                case '\n':
                    out.append("\\n");
                    break;
                case '\r':
                    out.append("\\r");
                    break;
                case '\t':
                    out.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                    break;
            }
        }
        out.append('"');
        return out.toString();
    }

    /**
     * Chat body: model, system + one user message, and the provider's
     * token field. Never carries history.
     */
    public static String chatBody(String model, String system,
            String question, String tokenField, int maxTokens) {
        return "{\"model\":" + quote(model) + ",\"messages\":["
                + "{\"role\":\"system\",\"content\":" + quote(system)
                + "},{\"role\":\"user\",\"content\":" + quote(question)
                + "}]," + quote(tokenField) + ":" + maxTokens + "}";
    }

    /**
     * Parses a chat reply. False {@link Reply#ok} on any malformed
     * shape (bad JSON, missing choices, missing message text).
     * An empty text is kept: the caller maps it to "No usable
     * answer" per the error table.
     */
    public static Reply parseReply(String body) {
        Object root;
        try {
            root = new Parser(body).parse();
        } catch (IllegalArgumentException bad) {
            return new Reply(false, "", null, false, -1, -1);
        }
        if (!(root instanceof Map)) {
            return new Reply(false, "", null, false, -1, -1);
        }
        Map<?, ?> top = (Map<?, ?>) root;
        Object choices = top.get("choices");
        if (!(choices instanceof List)
                || ((List<?>) choices).isEmpty()) {
            return new Reply(false, "", null, false, -1, -1);
        }
        Object first = ((List<?>) choices).get(0);
        if (!(first instanceof Map)) {
            return new Reply(false, "", null, false, -1, -1);
        }
        Map<?, ?> choice = (Map<?, ?>) first;
        Object message = choice.get("message");
        if (!(message instanceof Map)) {
            return new Reply(false, "", null, false, -1, -1);
        }
        Object content = ((Map<?, ?>) message).get("content");
        if (!(content instanceof String)) {
            return new Reply(false, "", null, false, -1, -1);
        }
        Object reason = choice.get("finish_reason");
        String finish =
                reason instanceof String ? (String) reason : null;
        Object usage = top.get("usage");
        if (!(usage instanceof Map)) {
            return new Reply(true, (String) content, finish,
                    false, -1, -1);
        }
        Map<?, ?> counts = (Map<?, ?>) usage;
        return new Reply(true, (String) content, finish, true,
                number(counts.get("prompt_tokens")),
                number(counts.get("completion_tokens")));
    }

    private static long number(Object value) {
        if (value instanceof Number) {
            return ((Number) value).longValue();
        }
        return -1;
    }

    /** Tiny strict parser: objects, arrays, strings, numbers, words. */
    private static final class Parser {
        private final String text;
        private int pos;

        Parser(String text) {
            this.text = text == null ? "" : text;
        }

        Object parse() {
            Object value = value();
            skip();
            if (pos != text.length()) {
                throw new IllegalArgumentException("trailing text");
            }
            return value;
        }

        private void skip() {
            while (pos < text.length()) {
                char c = text.charAt(pos);
                if (c != ' ' && c != '\t' && c != '\n' && c != '\r') {
                    return;
                }
                pos++;
            }
        }

        private Object value() {
            skip();
            if (pos >= text.length()) {
                throw new IllegalArgumentException("cut off");
            }
            char c = text.charAt(pos);
            if (c == '{') {
                return object();
            }
            if (c == '[') {
                return array();
            }
            if (c == '"') {
                return string();
            }
            if (c == '-' || (c >= '0' && c <= '9')) {
                return number();
            }
            return word();
        }

        private Map<String, Object> object() {
            Map<String, Object> out = new HashMap<String, Object>();
            pos++;
            skip();
            if (pos < text.length() && text.charAt(pos) == '}') {
                pos++;
                return out;
            }
            while (true) {
                skip();
                if (pos >= text.length()
                        || text.charAt(pos) != '"') {
                    throw new IllegalArgumentException("bad key");
                }
                String key = string();
                skip();
                if (pos >= text.length()
                        || text.charAt(pos) != ':') {
                    throw new IllegalArgumentException("no colon");
                }
                pos++;
                out.put(key, value());
                skip();
                if (pos >= text.length()) {
                    throw new IllegalArgumentException("cut off");
                }
                char c = text.charAt(pos);
                pos++;
                if (c == '}') {
                    return out;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("bad object");
                }
            }
        }

        private List<Object> array() {
            List<Object> out = new ArrayList<Object>();
            pos++;
            skip();
            if (pos < text.length() && text.charAt(pos) == ']') {
                pos++;
                return out;
            }
            while (true) {
                out.add(value());
                skip();
                if (pos >= text.length()) {
                    throw new IllegalArgumentException("cut off");
                }
                char c = text.charAt(pos);
                pos++;
                if (c == ']') {
                    return out;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("bad array");
                }
            }
        }

        private String string() {
            StringBuilder out = new StringBuilder();
            pos++;
            while (true) {
                if (pos >= text.length()) {
                    throw new IllegalArgumentException("cut off");
                }
                char c = text.charAt(pos);
                pos++;
                if (c == '"') {
                    return out.toString();
                }
                if (c != '\\') {
                    out.append(c);
                    continue;
                }
                if (pos >= text.length()) {
                    throw new IllegalArgumentException("cut off");
                }
                char e = text.charAt(pos);
                pos++;
                switch (e) {
                    case '"':
                        out.append('"');
                        break;
                    case '\\':
                        out.append('\\');
                        break;
                    case '/':
                        out.append('/');
                        break;
                    case 'n':
                        out.append('\n');
                        break;
                    case 'r':
                        out.append('\r');
                        break;
                    case 't':
                        out.append('\t');
                        break;
                    case 'u':
                        if (pos + 4 > text.length()) {
                            throw new IllegalArgumentException(
                                    "bad escape");
                        }
                        try {
                            out.append((char) Integer.parseInt(
                                    text.substring(pos, pos + 4), 16));
                        } catch (NumberFormatException bad) {
                            throw new IllegalArgumentException(
                                    "bad escape");
                        }
                        pos += 4;
                        break;
                    default:
                        throw new IllegalArgumentException(
                                "bad escape");
                }
            }
        }

        private Number number() {
            int start = pos;
            while (pos < text.length()) {
                char c = text.charAt(pos);
                if ((c < '0' || c > '9') && c != '-' && c != '+'
                        && c != '.' && c != 'e' && c != 'E') {
                    break;
                }
                pos++;
            }
            try {
                return Long.parseLong(text.substring(start, pos));
            } catch (NumberFormatException whole) {
                try {
                    return (long) Double.parseDouble(
                            text.substring(start, pos));
                } catch (NumberFormatException bad) {
                    throw new IllegalArgumentException("bad number");
                }
            }
        }

        private Object word() {
            if (text.startsWith("true", pos)) {
                pos += 4;
                return Boolean.TRUE;
            }
            if (text.startsWith("false", pos)) {
                pos += 5;
                return Boolean.FALSE;
            }
            if (text.startsWith("null", pos)) {
                pos += 4;
                return null;
            }
            throw new IllegalArgumentException("bad word");
        }
    }
}
