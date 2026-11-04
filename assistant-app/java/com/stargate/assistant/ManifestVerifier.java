package com.stargate.assistant;

import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/**
 * Pure-Java SHA-256 file check (no Android APIs, so the host test can run
 * this exact class on a JVM). The app refuses to load model files whose
 * hash differs from the manifest, even when name and size match.
 */
public final class ManifestVerifier {

    private ManifestVerifier() {
    }

    /** Lowercase hex SHA-256 of a file. */
    public static String sha256HexFile(String path) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IOException("SHA-256 unavailable", impossible);
        }
        byte[] buffer = new byte[1 << 20];
        InputStream in = new FileInputStream(path);
        try {
            int read;
            while ((read = in.read(buffer)) != -1) {
                digest.update(buffer, 0, read);
            }
        } finally {
            in.close();
        }
        StringBuilder out = new StringBuilder(64);
        for (byte b : digest.digest()) {
            out.append(Character.forDigit((b >> 4) & 0xF, 16));
            out.append(Character.forDigit(b & 0xF, 16));
        }
        return out.toString();
    }

    /** True only when the file's hash equals the expected lowercase hex. */
    public static boolean verify(String path, String expectedHex) {
        String actual;
        try {
            actual = sha256HexFile(path);
        } catch (IOException e) {
            return false;
        }
        return actual.equals(expectedHex);
    }

    /**
     * CLI for the host test: {@code ManifestVerifier <path> <expectedHex>}.
     * Prints MATCH/MISMATCH/ERROR and exits 0/1/2.
     */
    public static void main(String[] args) {
        if (args.length != 2) {
            System.out.println("ERROR expected 2 args");
            System.exit(2);
            return;
        }
        String actual;
        try {
            actual = sha256HexFile(args[0]);
        } catch (IOException e) {
            System.out.println("ERROR " + e.getMessage());
            System.exit(2);
            return;
        }
        if (actual.equals(args[1])) {
            System.out.println("MATCH " + actual);
            System.exit(0);
        } else {
            System.out.println("MISMATCH got=" + actual
                    + " expected=" + args[1]);
            System.exit(1);
        }
    }
}
