package com.stargate.hub;

/**
 * The assistant's engine, behind a seam. The Android build wires the
 * on-phone model and the cloud provider; host tests use {@link Fake}.
 * A key is only ever saved or removed here: it is never read back.
 */
public interface AskBackend {
    /** Name of the on-phone model, or null when this build has none. */
    String localName();

    /** Name of the cloud model and where it runs. */
    String cloudName();

    boolean keySet();

    void saveKey(String key) throws Exception;

    void deleteKey();

    /** Ask one question; mode is "local" or "cloud". */
    Answer ask(String mode, String question);

    /** One reply: either text or a plain error. */
    final class Answer {
        public final boolean ok;
        public final String text;

        public Answer(boolean ok, String text) {
            this.ok = ok;
            this.text = text;
        }
    }

    /** Canned engine for host tests and the preview server. */
    final class Fake implements AskBackend {
        private boolean key;
        public String local = "SmolLM2 135M";

        @Override
        public String localName() {
            return local;
        }

        @Override
        public String cloudName() {
            return "Cloud model (preview)";
        }

        @Override
        public boolean keySet() {
            return key;
        }

        @Override
        public void saveKey(String value) {
            key = true;
        }

        @Override
        public void deleteKey() {
            key = false;
        }

        @Override
        public Answer ask(String mode, String question) {
            if ("cloud".equals(mode) && !key) {
                return new Answer(false, "Add your cloud key first.");
            }
            return new Answer(true, "Preview answer to: " + question);
        }
    }
}
