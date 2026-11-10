package com.stargate.hub;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import javax.crypto.Cipher;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.PBEKeySpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * The password vault: one encrypted file on the phone (plain Java, so
 * the host tests run this exact file).
 *
 * <p>The master password is stretched with PBKDF2-HMAC-SHA256 into a
 * 256-bit key; the entries are sealed with AES-256-GCM under a fresh
 * random nonce on every save. The master password is never stored.
 * Without it the file cannot be read, and nobody can recover it.
 * The vault locks itself after five idle minutes, and wrong passwords
 * are slowed down more each time.
 */
public final class Vault {
    public static final int DEFAULT_ITERATIONS = 210000;
    public static final long IDLE_LOCK_MS = 5 * 60 * 1000L;
    public static final int MIN_PASSWORD = 8;
    public static final int MAX_ENTRIES = 1000;
    public static final int MAX_FIELD = 4000;
    static final String[] FIELDS = {
        "title", "username", "password", "url", "notes"};

    /** A refusal with words that can be shown to the person. */
    public static final class Refused extends Exception {
        public final int status;

        Refused(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    private final File file;
    private final int iterations;
    private final SecureRandom random = new SecureRandom();
    private byte[] key;
    private byte[] salt;
    private int rounds;
    private List<Map<String, Object>> entries;
    private long lastUsedMs;
    private int failures;
    private long blockedUntilMs;

    public Vault(File file) {
        this(file, DEFAULT_ITERATIONS);
    }

    public Vault(File file, int iterations) {
        this.file = file;
        this.iterations = iterations;
    }

    public synchronized boolean exists() {
        return file.isFile();
    }

    public synchronized boolean unlocked() {
        if (key != null && now() - lastUsedMs > IDLE_LOCK_MS) {
            lock();
        }
        return key != null;
    }

    public synchronized void lock() {
        if (key != null) {
            Arrays.fill(key, (byte) 0);
        }
        key = null;
        entries = null;
    }

    public synchronized void create(String password)
            throws Refused, IOException {
        if (exists()) {
            throw new Refused(409, "A vault already exists.");
        }
        checkNewPassword(password);
        salt = new byte[16];
        random.nextBytes(salt);
        rounds = iterations;
        key = derive(password, salt, rounds);
        entries = new ArrayList<Map<String, Object>>();
        lastUsedMs = now();
        save();
    }

    public synchronized void unlock(String password)
            throws Refused, IOException {
        if (!exists()) {
            throw new Refused(404, "No vault has been made yet.");
        }
        long wait = blockedUntilMs - now();
        if (wait > 0) {
            throw new Refused(429, "Too many wrong tries. Wait "
                    + (wait / 1000 + 1) + " seconds.");
        }
        Map<String, Object> stored;
        try {
            stored = Json.object(new String(
                    Files.readAllBytes(file.toPath()),
                    StandardCharsets.UTF_8));
        } catch (Json.Bad broken) {
            throw new Refused(500, "The vault file is damaged.");
        }
        try {
            Base64.Decoder b64 = Base64.getDecoder();
            byte[] fileSalt = b64.decode(Json.str(stored, "salt"));
            int fileRounds =
                    ((Number) stored.get("iterations")).intValue();
            if (fileRounds < 1000 || fileRounds > 10000000) {
                throw new Refused(500, "The vault file is damaged.");
            }
            byte[] tryKey = derive(password == null ? "" : password,
                    fileSalt, fileRounds);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE,
                    new SecretKeySpec(tryKey, "AES"),
                    new GCMParameterSpec(128,
                            b64.decode(Json.str(stored, "nonce"))));
            byte[] plain = cipher.doFinal(
                    b64.decode(Json.str(stored, "data")));
            Object parsed = Json.parse(
                    new String(plain, StandardCharsets.UTF_8));
            List<Map<String, Object>> loaded =
                    new ArrayList<Map<String, Object>>();
            for (Object item : (List<?>) parsed) {
                @SuppressWarnings("unchecked")
                Map<String, Object> entry = (Map<String, Object>) item;
                loaded.add(entry);
            }
            key = tryKey;
            salt = fileSalt;
            rounds = fileRounds;
            entries = loaded;
            failures = 0;
            lastUsedMs = now();
        } catch (GeneralSecurityException wrong) {
            failures++;
            if (failures >= 3) {
                long seconds = Math.min(300L,
                        1L << Math.min(failures - 2, 9));
                blockedUntilMs = now() + seconds * 1000L;
            }
            throw new Refused(403, "Wrong master password.");
        } catch (RuntimeException | Json.Bad broken) {
            throw new Refused(500, "The vault file is damaged.");
        }
    }

    public synchronized List<Map<String, Object>> list() throws Refused {
        needOpen();
        return new ArrayList<Map<String, Object>>(entries);
    }

    /** Add (no id) or replace (known id) one entry; returns its id. */
    public synchronized String put(Map<String, Object> incoming)
            throws Refused, IOException {
        needOpen();
        Map<String, Object> clean = new LinkedHashMap<String, Object>();
        String id = Json.str(incoming, "id");
        for (String field : FIELDS) {
            String value = Json.str(incoming, field);
            if (value == null) {
                value = "";
            }
            if (value.length() > MAX_FIELD) {
                throw new Refused(413, "That " + field + " is too long.");
            }
            clean.put(field, value);
        }
        if (((String) clean.get("title")).trim().isEmpty()) {
            throw new Refused(422, "Give the entry a name.");
        }
        clean.put("changed", Long.valueOf(now()));
        int at = -1;
        if (id != null) {
            for (int i = 0; i < entries.size(); i++) {
                if (id.equals(entries.get(i).get("id"))) {
                    at = i;
                }
            }
            if (at < 0) {
                throw new Refused(404, "That entry no longer exists.");
            }
        } else {
            if (entries.size() >= MAX_ENTRIES) {
                throw new Refused(413, "The vault is full.");
            }
            byte[] raw = new byte[9];
            random.nextBytes(raw);
            id = Base64.getUrlEncoder().withoutPadding()
                    .encodeToString(raw);
        }
        Map<String, Object> entry = new LinkedHashMap<String, Object>();
        entry.put("id", id);
        entry.putAll(clean);
        if (at >= 0) {
            entries.set(at, entry);
        } else {
            entries.add(entry);
        }
        save();
        return id;
    }

    public synchronized void remove(String id)
            throws Refused, IOException {
        needOpen();
        for (int i = 0; i < entries.size(); i++) {
            if (id.equals(entries.get(i).get("id"))) {
                entries.remove(i);
                save();
                return;
            }
        }
        throw new Refused(404, "That entry no longer exists.");
    }

    public synchronized void changePassword(String current, String next)
            throws Refused, IOException {
        needOpen();
        checkNewPassword(next);
        byte[] check = derive(current == null ? "" : current, salt,
                rounds);
        if (!java.security.MessageDigest.isEqual(check, key)) {
            throw new Refused(403, "Wrong master password.");
        }
        byte[] freshSalt = new byte[16];
        random.nextBytes(freshSalt);
        key = derive(next, freshSalt, rounds);
        salt = freshSalt;
        save();
    }

    private void needOpen() throws Refused {
        if (!unlocked()) {
            throw new Refused(423, "The vault is locked.");
        }
        lastUsedMs = now();
    }

    private static void checkNewPassword(String password) throws Refused {
        if (password == null || password.length() < MIN_PASSWORD) {
            throw new Refused(422, "Use a master password of at least "
                    + MIN_PASSWORD + " characters.");
        }
        if (password.length() > 1024) {
            throw new Refused(422, "That master password is too long.");
        }
    }

    private void save() throws IOException {
        try {
            byte[] nonce = new byte[12];
            random.nextBytes(nonce);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"),
                    new GCMParameterSpec(128, nonce));
            byte[] sealed = cipher.doFinal(Json.write(entries)
                    .getBytes(StandardCharsets.UTF_8));
            Base64.Encoder b64 = Base64.getEncoder();
            String text = Json.write(Json.map(
                    "v", Long.valueOf(1),
                    "kdf", "pbkdf2-hmac-sha256",
                    "cipher", "aes-256-gcm",
                    "iterations", Long.valueOf(rounds),
                    "salt", b64.encodeToString(salt),
                    "nonce", b64.encodeToString(nonce),
                    "data", b64.encodeToString(sealed)));
            Path target = file.toPath();
            Path tmp = Files.createTempFile(target.getParent(),
                    ".vault-", ".tmp");
            try {
                Files.write(tmp, text.getBytes(StandardCharsets.UTF_8));
                try {
                    Files.move(tmp, target,
                            StandardCopyOption.ATOMIC_MOVE,
                            StandardCopyOption.REPLACE_EXISTING);
                } catch (IOException plain) {
                    Files.move(tmp, target,
                            StandardCopyOption.REPLACE_EXISTING);
                }
            } finally {
                Files.deleteIfExists(tmp);
            }
        } catch (GeneralSecurityException failed) {
            throw new IOException("could not seal the vault", failed);
        }
    }

    private static byte[] derive(String password, byte[] salt, int rounds) {
        PBEKeySpec spec = new PBEKeySpec(password.toCharArray(), salt,
                rounds, 256);
        try {
            return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
                    .generateSecret(spec).getEncoded();
        } catch (GeneralSecurityException missing) {
            throw new IllegalStateException(missing);
        } finally {
            spec.clearPassword();
        }
    }

    private static long now() {
        return System.currentTimeMillis();
    }
}
