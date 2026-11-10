package com.stargate.hub;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/** File-name rules for the files folder (plain Java). */
public final class Names {
    private static final int MAX_NAME_LENGTH = 255;

    private Names() {
    }

    /** A refused name. */
    public static final class Refused extends Exception {
        Refused(String message) {
            super(message);
        }
    }

    /**
     * Validate one raw name from a URL. Refuses empty names, dot-dot,
     * slashes and backslashes at any percent-encoding level, and
     * control characters. Returns the decoded safe name.
     */
    public static String safeName(String raw) throws Refused {
        if (raw == null || raw.isEmpty()) {
            throw new Refused("empty file name");
        }
        String current = raw;
        for (int round = 0; round < 3; round++) {
            if (current.contains("/") || current.contains("\\")) {
                throw new Refused("path separator refused");
            }
            if (!current.contains("%")) {
                break;
            }
            current = percentDecodeOnce(current);
        }
        if (current.contains("/") || current.contains("\\")) {
            throw new Refused("path separator refused");
        }
        if (current.trim().isEmpty() || current.equals(".")) {
            throw new Refused("empty file name");
        }
        if (current.contains("..")) {
            throw new Refused("dot-dot refused");
        }
        if (current.length() > MAX_NAME_LENGTH) {
            throw new Refused("file name too long");
        }
        for (int i = 0; i < current.length(); i++) {
            char c = current.charAt(i);
            if (c < 0x20 || c == 0x7f) {
                throw new Refused("bad file name");
            }
        }
        return current;
    }

    static String percentDecodeOnce(String text) throws Refused {
        ByteArrayOutputStream decoded = new ByteArrayOutputStream();
        byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
        for (int i = 0; i < bytes.length; i++) {
            int c = bytes[i] & 0xff;
            if (c == '%') {
                if (i + 2 >= bytes.length) {
                    throw new Refused("bad percent escape");
                }
                int hi = Character.digit((char) bytes[i + 1], 16);
                int lo = Character.digit((char) bytes[i + 2], 16);
                if (hi < 0 || lo < 0) {
                    throw new Refused("bad percent escape");
                }
                int value = hi * 16 + lo;
                if (value == 0) {
                    throw new Refused("NUL refused");
                }
                decoded.write(value);
                i += 2;
            } else {
                decoded.write(c);
            }
        }
        return new String(decoded.toByteArray(), StandardCharsets.UTF_8);
    }

    /** Percent-encode a name for a response header. */
    public static String encode(String name) {
        StringBuilder out = new StringBuilder();
        for (byte b : name.getBytes(StandardCharsets.UTF_8)) {
            int c = b & 0xff;
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                    || (c >= '0' && c <= '9') || c == '.' || c == '-'
                    || c == '_') {
                out.append((char) c);
            } else {
                out.append(String.format("%%%02X", c));
            }
        }
        return out.toString();
    }

    private static String extension(String name) {
        int dot = name.lastIndexOf('.');
        return dot < 0 ? "" : name.substring(dot + 1)
                .toLowerCase(Locale.US);
    }

    /** photo, music, video or file: how the page shows it. */
    public static String kind(String name) {
        String type = inlineType(name);
        if (type == null) {
            return "file";
        }
        if (type.startsWith("image/")) {
            return "photo";
        }
        return type.startsWith("audio/") ? "music" : "video";
    }

    /**
     * The type for files a browser may show in the page: pictures,
     * music and video only. Everything else (including HTML and SVG,
     * which could run scripts) is only ever offered as a download.
     */
    public static String inlineType(String name) {
        switch (extension(name)) {
            case "jpg":
            case "jpeg":
                return "image/jpeg";
            case "png":
                return "image/png";
            case "gif":
                return "image/gif";
            case "webp":
                return "image/webp";
            case "mp3":
                return "audio/mpeg";
            case "m4a":
                return "audio/mp4";
            case "ogg":
            case "oga":
                return "audio/ogg";
            case "wav":
                return "audio/wav";
            case "flac":
                return "audio/flac";
            case "mp4":
            case "m4v":
                return "video/mp4";
            case "webm":
                return "video/webm";
            default:
                return null;
        }
    }
}
