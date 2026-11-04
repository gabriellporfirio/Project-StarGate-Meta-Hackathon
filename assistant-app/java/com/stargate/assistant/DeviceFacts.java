package com.stargate.assistant;

import java.util.regex.Pattern;

/**
 * App-side check for questions about this phone's live state (pure Java,
 * so the host test runs this exact class on a JVM).
 *
 * <p>The 1B model invents device facts (it answered "73%" to a battery
 * question despite the system message), so these questions never reach
 * the model: the app answers {@link #ANSWER} itself.
 *
 * <p>Matching is whole words, case-insensitive, against the single
 * {@link #TRIGGERS} list. Deliberately narrow cases: bare "battery"
 * is general knowledge ("Explain what a battery does" must reach the
 * model), so only battery state-of-charge phrases trigger; bare
 * "charge" is ambiguous (electric charge); bare "day" would eat
 * "rainy day hike" and bare "where"/"located" would eat geography
 * questions, so those use phrases instead.
 */
public final class DeviceFacts {

    /** Answer shown without calling the model. */
    public static final String ANSWER = "I'm an offline assistant and "
            + "can't see the phone's battery, sensors, files, time or "
            + "network.";

    /** Stats line shown with the app-side answer. */
    public static final String APP_STATS_LINE = "Answered by the app";

    /**
     * The only trigger list. Single words and phrases; every entry is
     * matched as whole words, case-insensitively.
     */
    public static final String[] TRIGGERS = {
        // Battery state of this phone.
        "battery level", "battery percent", "battery percentage",
        "battery charge", "battery status", "battery health",
        "battery life", "battery remaining",
        "my battery", "phone battery", "current battery",
        // Charge state.
        "charging", "charged", "charger",
        "charge level", "charge percent", "charge percentage",
        // Time and date.
        "time", "clock", "date", "today", "calendar",
        "what day", "day is it", "day is today",
        // Location.
        "location", "gps", "latitude", "longitude",
        "ip address", "my address", "where am i",
        // Signal and network.
        "signal", "network", "wifi", "wi-fi", "bluetooth",
        "cellular", "reception",
        // Storage.
        "storage", "disk", "free space", "disk space",
        // Sensors.
        "sensor", "sensors", "accelerometer", "gyroscope",
    };

    private static final Pattern[] PATTERNS = compile(TRIGGERS);

    private DeviceFacts() {
    }

    private static Pattern[] compile(String[] triggers) {
        Pattern[] patterns = new Pattern[triggers.length];
        for (int i = 0; i < triggers.length; i++) {
            patterns[i] = Pattern.compile(
                    "\\b" + Pattern.quote(triggers[i]) + "\\b",
                    Pattern.CASE_INSENSITIVE);
        }
        return patterns;
    }

    /** True when the question asks about this phone's live state. */
    public static boolean isDeviceFactQuestion(String question) {
        if (question == null) {
            return false;
        }
        String asked = question.replaceAll("\\s+", " ");
        for (Pattern pattern : PATTERNS) {
            if (pattern.matcher(asked).find()) {
                return true;
            }
        }
        return false;
    }

    /**
     * CLI for the host test: {@code DeviceFacts check <question>}
     * prints YES/NO, {@code DeviceFacts answer} prints the app-side
     * answer, {@code DeviceFacts stats} prints its stats line.
     */
    public static void main(String[] args) {
        if (args.length == 1 && args[0].equals("answer")) {
            System.out.println(ANSWER);
            return;
        }
        if (args.length == 1 && args[0].equals("stats")) {
            System.out.println(APP_STATS_LINE);
            return;
        }
        if (args.length == 2 && args[0].equals("check")) {
            System.out.println(isDeviceFactQuestion(args[1])
                    ? "YES" : "NO");
            return;
        }
        System.out.println(
                "ERROR expected 'check <question>', 'answer' or 'stats'");
        System.exit(2);
    }
}
