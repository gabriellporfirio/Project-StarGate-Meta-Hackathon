package com.stargate.hub;

import java.io.IOException;
import java.io.InputStream;

/** Where the pages come from: APK assets on the phone, a folder in tests. */
public interface Assets {
    /** Open one page file by its short name, or throw when missing. */
    InputStream open(String name) throws IOException;
}
