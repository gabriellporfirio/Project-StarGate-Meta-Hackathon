package com.stargate.assistant;

/**
 * Mode routing (pure Java): device-fact questions are answered by
 * the app in every mode, Local runs on the phone, and the two
 * cloud modes go to their provider. MainActivity switches on
 * {@link #route}; host tests run the real router plus the real
 * local flow against fakes to prove local mode makes zero
 * network requests.
 */
public final class ModeDispatch {

    public static final String APP = "app";
    public static final String LOCAL = "local";
    public static final String CLOUD = "cloud";

    private ModeDispatch() {
    }

    /** Where one question goes. Unknown modes stay local. */
    public static String route(String mode, String question) {
        if (DeviceFacts.isDeviceFactQuestion(question)) {
            return APP;
        }
        if (CloudSpec.MODE_META.equals(mode)
                || CloudSpec.MODE_OPEN.equals(mode)) {
            return CLOUD;
        }
        return LOCAL;
    }

    /** A cloud mode's provider; throws for anything else. */
    public static String providerFor(String mode) {
        if (CloudSpec.MODE_META.equals(mode)) {
            return CloudSpec.META;
        }
        if (CloudSpec.MODE_OPEN.equals(mode)) {
            return CloudSpec.BASETEN;
        }
        throw new IllegalArgumentException("not a cloud mode");
    }
}
