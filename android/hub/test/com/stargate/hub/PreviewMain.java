package com.stargate.hub;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Arrays;

/**
 * Runs the phone's real server on a computer with a pretend phone
 * behind it, so the pages can be looked at in a browser.
 *
 *   java com.stargate.hub.PreviewMain DATA PORT TOKEN SERVICES DIR...
 *
 * SERVICES is a comma list; pages are looked up in each DIR in turn.
 * Add "nolocal" to SERVICES for a phone too small to run a model.
 */
public final class PreviewMain {
    private PreviewMain() {
    }

    static Assets folders(final String[] dirs) {
        return new Assets() {
            @Override
            public InputStream open(String name) throws IOException {
                for (String dir : dirs) {
                    File file = new File(dir, name);
                    if (file.isFile()) {
                        return new FileInputStream(file);
                    }
                }
                throw new IOException("missing " + name);
            }
        };
    }

    public static void main(String[] args) throws Exception {
        String[] dirs = Arrays.copyOfRange(args, 4, args.length);
        AskBackend.Fake ask = new AskBackend.Fake();
        if (args[3].contains("nolocal")) {
            ask.local = null;
        }
        HubServer server = new HubServer(new File(args[0]),
                Integer.parseInt(args[1]), folders(dirs),
                new Device.Fake(), ask,
                Arrays.asList(args[3].split(",")), "PREVIEW-0001",
                args[2]);
        server.setReserveBytes(0);
        server.start();
        System.out.println("code " + server.getCode());
        System.out.flush();
        Thread.sleep(Long.MAX_VALUE);
    }
}
