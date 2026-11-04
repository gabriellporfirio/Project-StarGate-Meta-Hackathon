package com.stargate.assistant;

/**
 * "Which mode and model answered" label (pure Java). The screen
 * always shows this for the latest answer, in every mode.
 */
public final class ModeLabel {

    private ModeLabel() {
    }

    /** e.g. "Meta · muse-spark-1.3", or "Local · app". */
    public static String label(String mode, String model) {
        return mode + " · " + model;
    }
}
