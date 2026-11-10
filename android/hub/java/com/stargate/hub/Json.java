package com.stargate.hub;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Small JSON reader and writer (plain Java, no Android classes, so
 * the same file runs in the host tests). Objects are LinkedHashMap,
 * arrays are ArrayList, numbers are Long or Double.
 */
public final class Json {
    private static final int MAX_DEPTH = 32;

    private Json() {
    }

    /** Thrown for text that is not valid JSON. */
    public static final class Bad extends Exception {
        Bad(String message) {
            super(message);
        }
    }

    public static Object parse(String text) throws Bad {
        Parser parser = new Parser(text);
        parser.space();
        Object value = parser.value(0);
        parser.space();
        if (parser.at < text.length()) {
            throw new Bad("trailing text");
        }
        return value;
    }

    /** Parse text that must be a JSON object. */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> object(String text) throws Bad {
        Object value = parse(text);
        if (!(value instanceof Map)) {
            throw new Bad("not an object");
        }
        return (Map<String, Object>) value;
    }

    public static Map<String, Object> map(Object... pairs) {
        Map<String, Object> made = new LinkedHashMap<String, Object>();
        for (int i = 0; i + 1 < pairs.length; i += 2) {
            made.put((String) pairs[i], pairs[i + 1]);
        }
        return made;
    }

    public static String str(Map<String, Object> from, String key) {
        Object value = from == null ? null : from.get(key);
        return value instanceof String ? (String) value : null;
    }

    public static String write(Object value) {
        StringBuilder out = new StringBuilder();
        write(out, value);
        return out.toString();
    }

    private static void write(StringBuilder out, Object value) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof String) {
            quote(out, (String) value);
        } else if (value instanceof Number || value instanceof Boolean) {
            out.append(value.toString());
        } else if (value instanceof Map) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                quote(out, String.valueOf(entry.getKey()));
                out.append(':');
                write(out, entry.getValue());
            }
            out.append('}');
        } else if (value instanceof List) {
            out.append('[');
            boolean first = true;
            for (Object item : (List<?>) value) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                write(out, item);
            }
            out.append(']');
        } else {
            quote(out, value.toString());
        }
    }

    private static void quote(StringBuilder out, String text) {
        out.append('"');
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
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
                    if (c < 0x20 || c == 0x2028 || c == 0x2029) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        out.append('"');
    }

    private static final class Parser {
        final String text;
        int at;

        Parser(String text) {
            this.text = text;
        }

        void space() {
            while (at < text.length()) {
                char c = text.charAt(at);
                if (c != ' ' && c != '\n' && c != '\r' && c != '\t') {
                    break;
                }
                at++;
            }
        }

        char next() throws Bad {
            if (at >= text.length()) {
                throw new Bad("unexpected end");
            }
            return text.charAt(at);
        }

        Object value(int depth) throws Bad {
            if (depth > MAX_DEPTH) {
                throw new Bad("too deep");
            }
            char c = next();
            if (c == '{') {
                at++;
                Map<String, Object> made =
                        new LinkedHashMap<String, Object>();
                space();
                if (next() == '}') {
                    at++;
                    return made;
                }
                while (true) {
                    space();
                    if (next() != '"') {
                        throw new Bad("key expected");
                    }
                    String key = string();
                    space();
                    if (next() != ':') {
                        throw new Bad("colon expected");
                    }
                    at++;
                    space();
                    made.put(key, value(depth + 1));
                    space();
                    char sep = next();
                    at++;
                    if (sep == '}') {
                        return made;
                    }
                    if (sep != ',') {
                        throw new Bad("comma expected");
                    }
                }
            }
            if (c == '[') {
                at++;
                List<Object> made = new ArrayList<Object>();
                space();
                if (next() == ']') {
                    at++;
                    return made;
                }
                while (true) {
                    space();
                    made.add(value(depth + 1));
                    space();
                    char sep = next();
                    at++;
                    if (sep == ']') {
                        return made;
                    }
                    if (sep != ',') {
                        throw new Bad("comma expected");
                    }
                }
            }
            if (c == '"') {
                return string();
            }
            if (text.startsWith("true", at)) {
                at += 4;
                return Boolean.TRUE;
            }
            if (text.startsWith("false", at)) {
                at += 5;
                return Boolean.FALSE;
            }
            if (text.startsWith("null", at)) {
                at += 4;
                return null;
            }
            int start = at;
            while (at < text.length()
                    && "+-0123456789.eE".indexOf(text.charAt(at)) >= 0) {
                at++;
            }
            if (start == at) {
                throw new Bad("value expected");
            }
            String number = text.substring(start, at);
            try {
                if (number.indexOf('.') < 0 && number.indexOf('e') < 0
                        && number.indexOf('E') < 0) {
                    return Long.valueOf(number);
                }
                return Double.valueOf(number);
            } catch (NumberFormatException bad) {
                throw new Bad("bad number");
            }
        }

        String string() throws Bad {
            at++;
            StringBuilder out = new StringBuilder();
            while (true) {
                char c = next();
                at++;
                if (c == '"') {
                    return out.toString();
                }
                if (c != '\\') {
                    out.append(c);
                    continue;
                }
                char e = next();
                at++;
                switch (e) {
                    case 'n':
                        out.append('\n');
                        break;
                    case 'r':
                        out.append('\r');
                        break;
                    case 't':
                        out.append('\t');
                        break;
                    case 'b':
                        out.append('\b');
                        break;
                    case 'f':
                        out.append('\f');
                        break;
                    case 'u':
                        if (at + 4 > text.length()) {
                            throw new Bad("bad escape");
                        }
                        try {
                            out.append((char) Integer.parseInt(
                                    text.substring(at, at + 4), 16));
                        } catch (NumberFormatException bad) {
                            throw new Bad("bad escape");
                        }
                        at += 4;
                        break;
                    case '"':
                    case '\\':
                    case '/':
                        out.append(e);
                        break;
                    default:
                        throw new Bad("bad escape");
                }
            }
        }
    }
}
