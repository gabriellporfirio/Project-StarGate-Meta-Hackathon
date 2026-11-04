package com.stargate.files;

import java.io.File;

/**
 * Host harness for the Files handler tests. Runs the same
 * {@link FilesServer} the APK uses and prints one READY line:
 * {@code READY addr=127.0.0.1 port=<port> code=<code>}.
 */
public class FilesTestMain {
    public static void main(String[] args) throws Exception {
        File storeDir = null;
        int port = 0;
        long maxUpload = FilesServer.DEFAULT_MAX_UPLOAD_BYTES;
        long maxTotal = FilesServer.DEFAULT_MAX_TOTAL_BYTES;
        for (int i = 0; i < args.length; i++) {
            if (args[i].equals("--dump-limits")) {
                System.out.println("max_upload=" + maxUpload);
                System.out.println("max_total=" + maxTotal);
                return;
            } else if (args[i].equals("--storedir") && i + 1 < args.length) {
                storeDir = new File(args[++i]);
            } else if (args[i].equals("--port") && i + 1 < args.length) {
                port = Integer.parseInt(args[++i]);
            } else if (args[i].equals("--max-upload")
                    && i + 1 < args.length) {
                maxUpload = Long.parseLong(args[++i]);
            } else if (args[i].equals("--max-total")
                    && i + 1 < args.length) {
                maxTotal = Long.parseLong(args[++i]);
            } else {
                System.err.println("unknown argument: " + args[i]);
                System.exit(2);
            }
        }
        if (storeDir == null) {
            System.err.println("missing --storedir");
            System.exit(2);
        }
        File uploads = new File(storeDir, "uploads");
        File codeFile = new File(storeDir, "access-code");
        FilesServer server = new FilesServer(
                uploads, codeFile, port, maxUpload, maxTotal);
        server.start();
        System.out.println("READY addr=" + server.getBindAddress()
                + " port=" + server.getPort()
                + " code=" + server.getCode());
        System.out.flush();
        Object lock = new Object();
        synchronized (lock) {
            lock.wait();
        }
    }
}
