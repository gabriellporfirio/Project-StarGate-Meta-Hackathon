package com.stargate.assistant;

import java.util.HashMap;
import java.util.Map;

/**
 * Key storage seam (pure Java). The real store encrypts with an
 * Android Keystore key (AES-GCM); host tests use the memory store,
 * including its failing variant for the Keystore-failure test.
 */
public interface KeyStorage {

    /** Saves a key; throws without saving anything on failure. */
    void save(String provider, String key) throws Exception;

    /** Loads a key, or null when none is saved. */
    String load(String provider);

    /** Last four characters, or "" when none is saved. */
    String lastFour(String provider);

    /** Removes the key and its entry. */
    void delete(String provider);

    /** In-memory store for host tests. */
    final class Memory implements KeyStorage {
        private final Map<String, String> keys =
                new HashMap<String, String>();
        private boolean failOnSave;

        public void setFailOnSave(boolean fail) {
            failOnSave = fail;
        }

        @Override
        public synchronized void save(String provider, String key)
                throws Exception {
            if (failOnSave) {
                throw new Exception("Keystore failed");
            }
            keys.put(provider, key);
        }

        @Override
        public synchronized String load(String provider) {
            return keys.get(provider);
        }

        @Override
        public synchronized String lastFour(String provider) {
            String key = keys.get(provider);
            if (key == null || key.length() < 4) {
                return "";
            }
            return key.substring(key.length() - 4);
        }

        @Override
        public synchronized void delete(String provider) {
            keys.remove(provider);
        }
    }
}
