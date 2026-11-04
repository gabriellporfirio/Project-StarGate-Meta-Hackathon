package com.stargate.assistant;

import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * One-line answer summary. The token count comes from the runner's stats
 * JSON ({@code "generated_tokens"}: one of the native library's stats
 * keys, and the Mac proof screenshot showed the same JSON shape with
 * {@code "prompt_tokens"}); the time is measured by the app. When the
 * count is absent the line shows time only rather than inventing a number.
 */
public final class StatsLine {

    private static final Pattern GENERATED = Pattern.compile(
            "\"generated_tokens\"\\s*:\\s*(\\d+)");
    private static final String DOT = " \u00b7 ";


    private StatsLine() {
    }

    /**
     * Cloud line, e.g. "Meta · 34 tokens · 1.2 s", or "Meta · 1.2 s"
     * when the reply carried no usage. Negative tokens means unknown.
     */
    public static String formatCloud(String provider, long tokens,
            long elapsedMs) {
        String seconds = String.format(
                Locale.US, "%.1f s", elapsedMs / 1000.0);
        if (tokens < 0) {
            return provider + DOT + seconds;
        }
        return provider + DOT + tokens + " tokens" + DOT + seconds;
    }

    /** e.g. "Offline · 26 tokens · 0.7 s", or "Offline · 0.7 s". */
    public static String format(String statsJson, long elapsedMs) {
        String seconds = String.format(
                Locale.US, "%.1f s", elapsedMs / 1000.0);
        String tokens = null;
        if (statsJson != null) {
            try {
                Matcher found = GENERATED.matcher(statsJson);
                if (found.find()) {
                    tokens = found.group(1);
                }
            } catch (Exception ignored) {
                // Fall through to the time-only line.
            }
        }
        if (tokens == null) {
            return "Offline" + DOT + seconds;
        }
        return "Offline" + DOT + tokens + " tokens" + DOT + seconds;
    }

    /**
     * CLI for the host test:
     * {@code StatsLine format <elapsedMs> <statsJson>} or
     * {@code StatsLine cloud <provider> <tokens> <elapsedMs>}.
     */
    public static void main(String[] args) {
        if (args.length == 3 && args[0].equals("format")) {
            System.out.println(
                    format(args[2], Long.parseLong(args[1])));
            return;
        }
        if (args.length == 4 && args[0].equals("cloud")) {
            System.out.println(formatCloud(args[1],
                    Long.parseLong(args[2]),
                    Long.parseLong(args[3])));
            return;
        }
        System.out.println("ERROR expected 'format <elapsedMs>"
                + " <statsJson>' or 'cloud <provider> <tokens>"
                + " <elapsedMs>'");
        System.exit(2);
    }
}
