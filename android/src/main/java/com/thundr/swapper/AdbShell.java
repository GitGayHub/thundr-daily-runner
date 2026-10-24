package com.thundr.swapper;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;

/**
 * Minimal on-device ADB client (talks to the phone's own adbd on 127.0.0.1:5555).
 * It lets the app run shell-level commands (e.g. SIM power cycle) exactly like
 * "adb shell" from a PC. Requires "ADB over network" (adb tcpip 5555) and a one-time
 * "Allow USB debugging" confirmation for this app's key.
 */
public class AdbShell {

    private static final int A_CNXN = 0x4e584e43;
    private static final int A_AUTH = 0x48545541;
    private static final int A_OPEN = 0x4e45504f;
    private static final int A_OKAY = 0x59414b4f;
    private static final int A_CLSE = 0x45534c43;
    private static final int A_WRTE = 0x45545257;

    private static final int AUTH_TOKEN = 1;
    private static final int AUTH_SIGNATURE = 2;
    private static final int AUTH_RSAPUBLICKEY = 3;

    private static final int VERSION = 0x01000000;
    private static final int MAX_PAYLOAD = 4096;

    private static final byte[] SHA1_DIGEST_INFO = new byte[]{
            0x30, 0x21, 0x30, 0x09, 0x06, 0x05, 0x2b, 0x0e, 0x03, 0x02, 0x1a, 0x05, 0x00, 0x04, 0x14};

    private final Context ctx;
    private Socket socket;
    private InputStream in;
    private OutputStream out;
    private int nextLocalId = 1;

    public AdbShell(Context ctx) {
        this.ctx = ctx.getApplicationContext();
    }

    // ---------------------------------------------------------------- keys

    private KeyPair loadOrCreateKey() throws Exception {
        synchronized (AdbShell.class) {
            File f = new File(ctx.getFilesDir(), "adb_key.json");
            KeyFactory kf = KeyFactory.getInstance("RSA");
            if (f.exists()) {
                try {
                    org.json.JSONObject o = new org.json.JSONObject(new String(java.nio.file.Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8));
                    return new KeyPair(
                            kf.generatePublic(new X509EncodedKeySpec(Base64.decode(o.getString("pub"), Base64.NO_WRAP))),
                            kf.generatePrivate(new PKCS8EncodedKeySpec(Base64.decode(o.getString("priv"), Base64.NO_WRAP))));
                } catch (Exception ignored) {
                    // corrupted - regenerate below
                }
            }
            KeyPairGenerator g = KeyPairGenerator.getInstance("RSA");
            g.initialize(2048);
            KeyPair kp = g.generateKeyPair();
            org.json.JSONObject o = new org.json.JSONObject();
            o.put("priv", Base64.encodeToString(kp.getPrivate().getEncoded(), Base64.NO_WRAP));
            o.put("pub", Base64.encodeToString(kp.getPublic().getEncoded(), Base64.NO_WRAP));
            File tmp = new File(ctx.getFilesDir(), "adb_key.json.tmp");
            try (FileOutputStream fos = new FileOutputStream(tmp)) {
                fos.write(o.toString().getBytes(StandardCharsets.UTF_8));
            }
            //noinspection ResultOfMethodCallIgnored
            tmp.renameTo(f);
            return kp;
        }
    }
    /** Public key in the format adbd expects (struct RSAPublicKey, base64 + " name"). */
    private static byte[] androidPublicKey(RSAPublicKey key) {
        final int words = 64; // 2048 bit
        BigInteger n = key.getModulus();
        BigInteger r32 = BigInteger.ONE.shiftLeft(32);
        long n0inv = n.mod(r32).modInverse(r32).negate().mod(r32).longValue();
        BigInteger rr = BigInteger.ONE.shiftLeft(2048).pow(2).mod(n);

        ByteBuffer bb = ByteBuffer.allocate(4 + 4 + 256 + 256 + 4).order(ByteOrder.LITTLE_ENDIAN);
        bb.putInt(words);
        bb.putInt((int) n0inv);
        bb.put(toLittleEndian(n, 256));
        bb.put(toLittleEndian(rr, 256));
        bb.putInt(key.getPublicExponent().intValue());
        byte[] struct = bb.array();
        byte[] b64 = Base64.encode(struct, Base64.NO_WRAP);
        byte[] tail = " thundr-swapper@android\0".getBytes(StandardCharsets.UTF_8);
        byte[] res = new byte[b64.length + tail.length];
        System.arraycopy(b64, 0, res, 0, b64.length);
        System.arraycopy(tail, 0, res, b64.length, tail.length);
        return res;
    }

    private static byte[] toLittleEndian(BigInteger v, int len) {
        byte[] be = v.toByteArray();
        byte[] le = new byte[len];
        for (int i = 0; i < len; i++) {
            int src = be.length - 1 - i;
            le[i] = src >= 0 ? be[src] : 0;
        }
        return le;
    }

    // ------------------------------------------------------------ transport

    private void send(int cmd, int arg0, int arg1, byte[] data) throws Exception {
        int len = data == null ? 0 : data.length;
        ByteBuffer h = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN);
        h.putInt(cmd).putInt(arg0).putInt(arg1).putInt(len).putInt(0).putInt(cmd ^ 0xffffffff);
        out.write(h.array());
        if (len > 0) out.write(data);
        out.flush();
    }

    private static final class Msg {
        int cmd, arg0, arg1;
        byte[] data;
    }

    private Msg read() throws Exception {
        byte[] hdr = new byte[24];
        new DataInputStream(in).readFully(hdr);
        ByteBuffer h = ByteBuffer.wrap(hdr).order(ByteOrder.LITTLE_ENDIAN);
        Msg m = new Msg();
        m.cmd = h.getInt();
        m.arg0 = h.getInt();
        m.arg1 = h.getInt();
        int len = h.getInt();
        m.data = new byte[len];
        if (len > 0) new DataInputStream(in).readFully(m.data);
        return m;
    }

    /**
     * Connects and authenticates. Blocks up to authTimeoutMs while the user confirms
     * the "Allow USB debugging" dialog on first use.
     */
    public void connect(int authTimeoutMs) throws Exception {
        KeyPair kp = loadOrCreateKey();
        socket = new Socket();
        socket.connect(new InetSocketAddress("127.0.0.1", 5555), 3000);
        socket.setSoTimeout(authTimeoutMs);
        in = socket.getInputStream();
        out = socket.getOutputStream();

        send(A_CNXN, VERSION, MAX_PAYLOAD, "host::\0".getBytes(StandardCharsets.UTF_8));

        boolean sentSignature = false;
        boolean sentPublicKey = false;
        while (true) {
            Msg m = read();
            if (m.cmd == A_CNXN) {
                return;
            }
            if (m.cmd == A_AUTH && m.arg0 == AUTH_TOKEN) {
                if (!sentSignature) {
                    sentSignature = true;
                    Signature s = Signature.getInstance("NONEwithRSA");
                    s.initSign((PrivateKey) kp.getPrivate());
                    byte[] toSign = new byte[SHA1_DIGEST_INFO.length + m.data.length];
                    System.arraycopy(SHA1_DIGEST_INFO, 0, toSign, 0, SHA1_DIGEST_INFO.length);
                    System.arraycopy(m.data, 0, toSign, SHA1_DIGEST_INFO.length, m.data.length);
                    s.update(toSign);
                    send(A_AUTH, AUTH_SIGNATURE, 0, s.sign());
                } else if (!sentPublicKey) {
                    sentPublicKey = true;
                    send(A_AUTH, AUTH_RSAPUBLICKEY, 0, androidPublicKey((RSAPublicKey) kp.getPublic()));
                } else {
                    throw new Exception("ADB: доступ отклонён");
                }
            }
        }
    }

    /** Runs a shell command and returns its combined output. */
    public String shell(String command, int timeoutMs) throws Exception {
        socket.setSoTimeout(timeoutMs);
        int localId = nextLocalId++;
        send(A_OPEN, localId, 0, ("shell:" + command + "\0").getBytes(StandardCharsets.UTF_8));
        int remoteId = 0;
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        while (true) {
            Msg m = read();
            if (m.cmd == A_OKAY) {
                remoteId = m.arg0;
            } else if (m.cmd == A_WRTE) {
                bos.write(m.data);
                send(A_OKAY, localId, m.arg0, null);
            } else if (m.cmd == A_CLSE) {
                send(A_CLSE, localId, m.arg0, null);
                break;
            }
        }
        return bos.toString("UTF-8");
    }

    public void close() {
        try {
            if (socket != null) socket.close();
        } catch (Exception ignored) {
        }
    }

    // ------------------------------------------------------------- helpers

    /** Pushes a bundled asset to /data/local/tmp via base64 (no sync protocol needed). */
    public void pushAsset(String assetName, String remotePath) throws Exception {
        byte[] data;
        try (InputStream is = ctx.getAssets().open(assetName)) {
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = is.read(buf)) > 0) bos.write(buf, 0, n);
            data = bos.toByteArray();
        }
        String b64 = Base64.encodeToString(data, Base64.NO_WRAP);
        shell("rm -f " + remotePath, 5000);
        final int chunk = 2000;
        for (int i = 0; i < b64.length(); i += chunk) {
            String part = b64.substring(i, Math.min(b64.length(), i + chunk));
            shell("echo -n '" + part + "' >> " + remotePath + ".b64", 5000);
        }
        shell("base64 -d " + remotePath + ".b64 > " + remotePath + " && rm -f " + remotePath + ".b64 && chmod 644 " + remotePath, 5000);
    }
}
