package com.stargate.assistant;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.Charset;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * Keys encrypted with an Android Keystore key (AES-GCM), one
 * Keystore entry per provider plus one private blob file holding
 * IV + ciphertext. If the Keystore fails the key is not saved
 * and an error is shown; there is no plain-text fallback, and
 * nothing here ever writes the key unencrypted.
 */
public final class KeystoreKeyStorage implements KeyStorage {

    private static final Charset UTF8 = Charset.forName("UTF-8");

    private final Context context;

    public KeystoreKeyStorage(Context context) {
        this.context = context.getApplicationContext();
    }

    @Override
    public synchronized void save(String provider, String key)
            throws Exception {
        SecretKey entry = entry(provider);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, entry);
        byte[] iv = cipher.getIV();
        byte[] secret = cipher.doFinal(key.getBytes(UTF8));
        File target = blob(provider);
        FileOutputStream out = new FileOutputStream(target);
        try {
            out.write(iv.length);
            out.write(iv);
            out.write(secret);
        } finally {
            out.close();
        }
    }

    @Override
    public synchronized String load(String provider) {
        try {
            File target = blob(provider);
            if (!target.isFile()) {
                return null;
            }
            byte[] raw = readFully(target);
            if (raw.length < 2) {
                return null;
            }
            int ivLen = raw[0] & 0xFF;
            if (raw.length < 1 + ivLen) {
                return null;
            }
            byte[] iv = new byte[ivLen];
            System.arraycopy(raw, 1, iv, 0, ivLen);
            byte[] secret = new byte[raw.length - 1 - ivLen];
            System.arraycopy(raw, 1 + ivLen, secret, 0,
                    secret.length);
            Cipher cipher =
                    Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, entry(provider),
                    new GCMParameterSpec(128, iv));
            return new String(cipher.doFinal(secret), UTF8);
        } catch (Exception failed) {
            return null;
        }
    }

    @Override
    public synchronized String lastFour(String provider) {
        String key = load(provider);
        if (key == null || key.length() < 4) {
            return "";
        }
        return key.substring(key.length() - 4);
    }

    @Override
    public synchronized void delete(String provider) {
        blob(provider).delete();
        try {
            KeyStore store = KeyStore.getInstance("AndroidKeyStore");
            store.load(null);
            store.deleteEntry(alias(provider));
        } catch (Exception failed) {
            // Best effort: the blob is already gone, so the key
            // is unusable without its entry either way.
        }
    }

    private SecretKey entry(String provider) throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        String alias = alias(provider);
        if (store.containsAlias(alias)) {
            return (SecretKey) store.getKey(alias, null);
        }
        KeyGenerator gen = KeyGenerator.getInstance(
                KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        gen.init(new KeyGenParameterSpec.Builder(alias,
                KeyProperties.PURPOSE_ENCRYPT
                        | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(
                        KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build());
        return gen.generateKey();
    }

    private String alias(String provider) {
        return "m4c-key-" + provider;
    }

    private File blob(String provider) {
        return new File(context.getFilesDir(),
                "key-" + provider + ".bin");
    }

    private static byte[] readFully(File target) throws Exception {
        FileInputStream in = new FileInputStream(target);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        try {
            while (true) {
                int got = in.read(buf);
                if (got < 0) {
                    break;
                }
                out.write(buf, 0, got);
            }
        } finally {
            in.close();
        }
        return out.toByteArray();
    }
}
