package com.stargate.assistant;

/**
 * Streaming answer state. Two separate jobs (tested separately):
 *
 * <p>(a) Ending generation: {@link #append} returns true once a stop
 * marker (end-of-turn or end-of-text) has arrived, and the caller then
 * stops the runner, so generation ends at either token even though this
 * ExecuTorch version offers no stop-token-ID list.
 *
 * <p>(b) Display filtering: {@link #display} never contains control-token
 * text, even when the callback delivers the terminating token itself
 * (ExecuTorch 1.5.1 calls the text callback before its end-of-sequence
 * check, so the marker text reaches the app).
 *
 * <p>The raw buffer is re-scanned on every chunk, so markers split
 * across callbacks are still caught.
 */
public final class AnswerAccumulator {

    private final StringBuilder raw = new StringBuilder();
    private boolean stopped = false;

    /** Feed one callback chunk. Returns true when the runner must stop. */
    public boolean append(String chunk) {
        if (stopped) {
            return true;
        }
        if (chunk != null) {
            raw.append(chunk);
        }
        if (ChatFormat.stopIndex(raw.toString()) >= 0) {
            stopped = true;
        }
        return stopped;
    }

    /** True once a stop marker has arrived. */
    public boolean stopped() {
        return stopped;
    }

    /** Answer text safe to show (control tokens filtered out). */
    public String display() {
        return ChatFormat.displayText(raw.toString());
    }

    /**
     * CLI for the host test: {@code AnswerAccumulator feed <chunk>...}
     * prints one {@code chunk <i> stop=<flag>} line per chunk and a final
     * {@code display: <text>} line.
     */
    public static void main(String[] args) {
        if (args.length < 1 || !args[0].equals("feed")) {
            System.out.println("ERROR expected 'feed <chunk>...'");
            System.exit(2);
            return;
        }
        AnswerAccumulator acc = new AnswerAccumulator();
        for (int i = 1; i < args.length; i++) {
            System.out.println("chunk " + (i - 1) + " stop="
                    + acc.append(args[i]));
        }
        System.out.println("display: " + acc.display());
    }
}
