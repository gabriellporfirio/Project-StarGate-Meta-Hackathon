package com.stargate.assistant;

import java.io.File;

/**
 * Route B model-folder creation and per-path model-file checks (pure Java,
 * so the host test runs this exact class on a JVM).
 *
 * <p>Creation is for Route B only. Route A's read-only image path is only
 * opened in place, never created or copied here. mkdirs cannot repair a
 * folder owned by someone else, so every creation or readability failure
 * is reported and nothing here claims the folder is app-owned.
 */
public final class ModelFiles {

    private ModelFiles() {
    }

    /**
     * Create the Route B folder when missing. Returns null when the folder
     * is usable, else the failure ("folder missing (could not create): …").
     */
    public static String ensureDir(String path) {
        File dir = new File(path);
        if (dir.isDirectory()) {
            return null;
        }
        if (dir.mkdirs() || dir.isDirectory()) {
            return null;
        }
        return "folder missing (could not create): " + path;
    }

    /**
     * Check one expected file. Returns null when it is present, readable
     * and exactly the expected size, else "file missing: …", "not
     * readable: …" or "wrong size: … (expected … bytes, got …)".
     */
    public static String checkFile(String path, long expectedBytes) {
        File file = new File(path);
        String name = file.getName();
        if (!file.isFile()) {
            return "file missing: " + name;
        }
        if (!file.canRead()) {
            return "not readable: " + name;
        }
        long actual = file.length();
        if (actual != expectedBytes) {
            return "wrong size: " + name + " (expected " + expectedBytes
                    + " bytes, got " + actual + ")";
        }
        return null;
    }

    /**
     * Report lines for one candidate folder: a single "folder missing: …"
     * or "not readable: …" line when the folder itself cannot be used,
     * else one line per expected file ("OK: &lt;name&gt;" or the check
     * failure). Never creates anything.
     */
    public static String[] checkDir(String dirPath, String pteName,
            long pteBytes, String tokName, long tokBytes) {
        File dir = new File(dirPath);
        if (!dir.isDirectory()) {
            return new String[]{"folder missing: " + dirPath};
        }
        if (!dir.canRead()) {
            return new String[]{"not readable: " + dirPath};
        }
        String pte = checkFile(
                new File(dir, pteName).getAbsolutePath(), pteBytes);
        String tok = checkFile(
                new File(dir, tokName).getAbsolutePath(), tokBytes);
        return new String[]{
            pte == null ? "OK: " + pteName : pte,
            tok == null ? "OK: " + tokName : tok,
        };
    }

    /** True when every checkDir line starts with "OK: ". */
    public static boolean dirReady(String[] report) {
        for (String line : report) {
            if (!line.startsWith("OK: ")) {
                return false;
            }
        }
        return true;
    }

    /**
     * CLI for the host test: {@code ModelFiles ensure <dir>},
     * {@code ModelFiles check <path> <bytes>} or
     * {@code ModelFiles checkdir <dir> <pte> <pteBytes> <tok> <tokBytes>}.
     * Prints OK lines or the failure; exits 0 only when ready.
     */
    public static void main(String[] args) {
        if (args.length == 2 && args[0].equals("ensure")) {
            String failure = ensureDir(args[1]);
            System.out.println(failure == null ? "OK" : failure);
            System.exit(failure == null ? 0 : 1);
            return;
        }
        if (args.length == 3 && args[0].equals("check")) {
            String failure = checkFile(args[1], Long.parseLong(args[2]));
            System.out.println(failure == null ? "OK" : failure);
            System.exit(failure == null ? 0 : 1);
            return;
        }
        if (args.length == 6 && args[0].equals("checkdir")) {
            String[] report = checkDir(args[1], args[2],
                    Long.parseLong(args[3]), args[4],
                    Long.parseLong(args[5]));
            for (String line : report) {
                System.out.println(line);
            }
            System.exit(dirReady(report) ? 0 : 1);
            return;
        }
        System.out.println(
                "ERROR expected 'ensure <dir>', 'check <path> <bytes>' or "
                + "'checkdir <dir> <pte> <pteBytes> <tok> <tokBytes>'");
        System.exit(2);
    }
}
