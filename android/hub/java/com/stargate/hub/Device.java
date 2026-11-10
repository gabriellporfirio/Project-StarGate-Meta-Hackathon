package com.stargate.hub;

/**
 * What the hub needs from the phone itself. The Android build wires
 * this to the real device; the host tests use {@link Fake}.
 */
public interface Device {
    /** Battery percent 0-100, or -1 when unknown. */
    int batteryPercent();

    boolean pluggedIn();

    /** True when the phone has Wi-Fi switched on. */
    boolean networkOn();

    /** True when this build is allowed to switch the network. */
    boolean canSwitchNetwork();

    /** Switch Wi-Fi; returns false when the phone refused. */
    boolean setNetwork(boolean on);

    /** True when this build is allowed to reboot the phone. */
    boolean canReboot();

    boolean reboot();

    /** In-memory device for host tests and the preview server. */
    final class Fake implements Device {
        public boolean network = true;
        public boolean rebooted;

        @Override
        public int batteryPercent() {
            return 100;
        }

        @Override
        public boolean pluggedIn() {
            return true;
        }

        @Override
        public boolean networkOn() {
            return network;
        }

        @Override
        public boolean canSwitchNetwork() {
            return true;
        }

        @Override
        public boolean setNetwork(boolean on) {
            network = on;
            return true;
        }

        @Override
        public boolean canReboot() {
            return true;
        }

        @Override
        public boolean reboot() {
            rebooted = true;
            return true;
        }
    }
}
