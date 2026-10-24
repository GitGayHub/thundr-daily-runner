package com.thundr.swapper;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebStorage;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.ProgressBar;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.OutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * In-app Thundr browser. Logs the chosen account in through the id.thundr.com API,
 * writes the same localStorage entries the website itself writes after login
 * ("user" and "settings"), and opens thundr.com with camera/microphone (WebRTC) enabled.
 */
public class BrowserActivity extends Activity {

    public static final String EXTRA_USERNAME = "extra_username";
    public static final String EXTRA_USER_ID = "extra_user_id";
    public static final String EXTRA_TOKEN = "extra_token";
    public static final String EXTRA_EMAIL = "extra_email";
    public static final String EXTRA_PASSWORD = "extra_password";
    public static final String EXTRA_BALANCE = "extra_balance";
    public static final String EXTRA_MINUTES = "extra_minutes";
    public static final String EXTRA_BOOKMARKLET = "extra_bookmarklet";

    private static final String USER_AGENT = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36";
    private static final String ORIGIN = "https://thundr.com";
    private static final String SEED_URL = ORIGIN + "/robots.txt";
    private static final String DEFAULT_SETTINGS =
            "{\"device\":null,\"userDevice\":null,\"platform\":\"web\",\"volume\":{\"master\":0,\"output\":100},"
            + "\"autoRoll\":{\"video\":true,\"text\":false},\"performance\":{\"lowPower\":false,\"facePopup\":true},"
            + "\"interests\":{\"wait\":0,\"tags\":[]},\"filters\":{\"countries\":[],\"regions\":[],\"sex\":[],\"max_wait\":3},"
            + "\"autoMod\":true,\"mobile\":{\"swipeSensitivity\":0,\"swipeSkip\":true,\"haptics\":true},"
            + "\"privacy\":{\"hidden\":false},\"event\":{\"looking_for\":\"a\"},\"reviewed\":null,\"notification\":null}";

    private String username;
    private String userId;
    private String hashedToken;
    private String email;
    private String password;
    private int balance;
    private int minutes;
    private String sessionJson;

    // Session payload prepared for injection
    private String userJson;
    private String settingsJson;
    private boolean seeded = false;

    private TextView tvBrowserTitle;
    private Button btnBack;
    private Button btnReload;
    private ProgressBar progressBar;
    private WebView webView;
    private PermissionRequest pendingWebPermission;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().setFlags(
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED
        );
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        setContentView(R.layout.activity_browser);

        username = getIntent().getStringExtra(EXTRA_USERNAME);
        userId = getIntent().getStringExtra(EXTRA_USER_ID);
        hashedToken = getIntent().getStringExtra(EXTRA_TOKEN);
        email = getIntent().getStringExtra(EXTRA_EMAIL);
        password = getIntent().getStringExtra(EXTRA_PASSWORD);
        balance = getIntent().getIntExtra(EXTRA_BALANCE, 0);
        minutes = getIntent().getIntExtra(EXTRA_MINUTES, 0);
        sessionJson = getIntent().getStringExtra(EXTRA_BOOKMARKLET);

        tvBrowserTitle = findViewById(R.id.tvBrowserAccountTitle);
        btnBack = findViewById(R.id.btnBackToAccounts);
        btnReload = findViewById(R.id.btnReloadPage);
        progressBar = findViewById(R.id.browserProgressBar);
        webView = findViewById(R.id.browserWebView);

        tvBrowserTitle.setText("👤 " + username + " (" + minutes + " мин | " + balance + " 🪙)");

        btnBack.setOnClickListener(v -> finish());
        btnReload.setOnClickListener(v -> webView.reload());

        requestCameraAndAudioPermissions();
        WebView.setWebContentsDebuggingEnabled(true);
        initWebView();
        startSession();
    }

    private boolean hasMediaPermissions() {
        return checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
                && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    private void requestCameraAndAudioPermissions() {
        if (!hasMediaPermissions()) {
            requestPermissions(new String[]{
                    Manifest.permission.CAMERA,
                    Manifest.permission.RECORD_AUDIO,
                    Manifest.permission.MODIFY_AUDIO_SETTINGS
            }, 101);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (pendingWebPermission != null) {
            PermissionRequest r = pendingWebPermission;
            pendingWebPermission = null;
            try {
                if (hasMediaPermissions()) r.grant(r.getResources()); else r.deny();
            } catch (Exception e) {
                e.printStackTrace();
            }
        }
    }

    /** Wipes previous account data, logs in via API (background), then seeds localStorage and opens the site. */
    private void startSession() {
        CookieManager.getInstance().removeAllCookies(null);
        WebStorage.getInstance().deleteAllData();
        progressBar.setVisibility(View.VISIBLE);

        final boolean cycleSim = getSharedPreferences("thundr_swapper_prefs", Context.MODE_PRIVATE)
                .getBoolean(MainActivity.PREF_SIM_CYCLE, false);

        new Thread(() -> {
            if (cycleSim) cycleSimCard();
            String u = null;
            String s = null;
            try {
                if (password != null && !password.isEmpty()) {
                    JSONObject res = apiLogin(email != null && !email.isEmpty() ? email : username, password);
                    JSONObject data = res.getJSONObject("data").getJSONObject("data");
                    String token = res.optString("hashed_token", hashedToken);
                    JSONObject settings = new JSONObject(DEFAULT_SETTINGS);
                    JSONObject srv = data.optJSONObject("settings");
                    if (srv != null) {
                        java.util.Iterator<String> it = srv.keys();
                        while (it.hasNext()) {
                            String k = it.next();
                            settings.put(k, srv.get(k));
                        }
                    }
                    JSONObject profile = data.optJSONObject("profile");
                    if (profile != null) settings.put("profile", profile);
                    // "Disable Face Popup" is always ON by default
                    forceNoFacePopup(settings);
                    if (srv == null) srv = new JSONObject();
                    forceNoFacePopup(srv);

                    JSONObject user = new JSONObject();
                    user.put("created_at", data.opt("created_at"));
                    user.put("id", data.optString("key", userId));
                    user.put("email", username);
                    user.put("verification", data.optBoolean("verification", false));
                    user.put("onboarded", true);
                    user.put("hashed_token", token);
                    user.put("settings", srv);
                    if (profile != null) user.put("profile", profile);
                    u = user.toString();
                    s = settings.toString();
                }
            } catch (Exception e) {
                android.util.Log.w("ThundrWeb", "API login failed, using cached session: " + e);
            }

            if (u == null) {
                // Fallback: cached token from accounts.json with default settings
                try {
                    JSONObject settings = new JSONObject(DEFAULT_SETTINGS);
                    JSONObject profile = new JSONObject();
                    profile.put("sex", "m");
                    profile.put("looking_for", "f");
                    profile.put("dob", "2000-01-01");
                    settings.put("profile", profile);
                    JSONObject user = new JSONObject();
                    user.put("created_at", System.currentTimeMillis());
                    user.put("id", userId);
                    user.put("email", username);
                    user.put("verification", false);
                    user.put("onboarded", true);
                    user.put("hashed_token", hashedToken);
                    user.put("settings", new JSONObject(DEFAULT_SETTINGS));
                    user.put("profile", profile);
                    u = user.toString();
                    s = settings.toString();
                } catch (Exception ignored) {
                }
            }

            userJson = u;
            settingsJson = s;
            runOnUiThread(() -> webView.loadUrl(SEED_URL));
        }).start();
    }

    private static void forceNoFacePopup(JSONObject settings) throws org.json.JSONException {
        JSONObject perf = settings.optJSONObject("performance");
        if (perf == null) perf = new JSONObject();
        perf.put("facePopup", true);
        settings.put("performance", perf);
    }

    /**
     * Power-cycles the active SIM (0.5 s off, then on) to get a new mobile IP,
     * using the same shell tool as vpn-proxy-manager (sim_tool.dex) through the phone's own adbd.
     */
    private void cycleSimCard() {
        setTitleText("🔄 SIM: перезагрузка...");
        AdbShell adb = new AdbShell(this);
        try {
            adb.connect(60000);
            String dex = "/data/local/tmp/sim_tool.dex";
            String ls = adb.shell("ls " + dex + " 2>/dev/null", 5000).trim();
            if (!ls.contains("sim_tool.dex")) adb.pushAsset("sim_tool.dex", dex);

            int subId = 4;
            String reg = adb.shell("dumpsys telephony.registry | grep -m 1 mActiveDataSubId", 8000);
            int idx = reg.indexOf("mActiveDataSubId=");
            if (idx >= 0) {
                try {
                    int v = Integer.parseInt(reg.substring(idx + 17).trim().split("\\s+")[0]);
                    if (v > 0) subId = v;
                } catch (Exception ignored) {
                }
            }

            String out = adb.shell("app_process -Djava.class.path=" + dex + " /data/local/tmp SimTool cycle " + subId + " 500", 20000);
            if (!out.contains("CYCLE_COMPLETE")) {
                // Fallback: soft mobile-data restart
                adb.shell("svc data disable; sleep 0.5; svc data enable", 10000);
            }
            waitForNetwork(25000);
            setTitleText("✅ SIM перезагружена");
        } catch (java.net.ConnectException e) {
            setTitleText("⚠️ SIM: нет ADB (adb tcpip 5555)");
        } catch (Exception e) {
            android.util.Log.w("ThundrWeb", "SIM cycle failed: " + e);
            setTitleText("⚠️ SIM: " + e.getMessage());
        } finally {
            adb.close();
        }
        try {
            Thread.sleep(600);
        } catch (InterruptedException ignored) {
        }
        runOnUiThread(() -> tvBrowserTitle.setText("👤 " + username + " (" + minutes + " мин | " + balance + " 🪙)"));
    }

    private void setTitleText(final String t) {
        runOnUiThread(() -> tvBrowserTitle.setText(t));
    }

    private void waitForNetwork(int timeoutMs) {
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        long end = System.currentTimeMillis() + timeoutMs;
        try {
            Thread.sleep(1500); // let the radio actually drop first
            while (System.currentTimeMillis() < end) {
                android.net.NetworkInfo ni = cm.getActiveNetworkInfo();
                if (ni != null && ni.isConnected()) {
                    java.net.HttpURLConnection c = (java.net.HttpURLConnection) new URL("https://id.thundr.com/").openConnection();
                    c.setConnectTimeout(3000);
                    c.setReadTimeout(3000);
                    try {
                        c.getResponseCode();
                        return;
                    } catch (Exception ignored) {
                    } finally {
                        c.disconnect();
                    }
                }
                Thread.sleep(700);
            }
        } catch (Exception ignored) {
        }
    }

    private static final String POPUP_KILLER_JS = "(function(){if(window.__tswPopup)return;window.__tswPopup=1;"
            + "function kill(){try{"
            + "document.querySelectorAll('div.fixed').forEach(function(d){"
            + "var t=d.textContent||'';"
            + "if(/Today.s Forecast/i.test(t)&&/Google Play|Click for/i.test(t)){"
            + "var b=d.querySelector('button.btn-circle');if(b){b.click();}else{d.style.display='none';}}"
            + "});"
            + "document.querySelectorAll('button').forEach(function(b){"
            + "if(/^\\s*Don.t show again\\s*$/i.test(b.textContent||'')){b.click();}});"
            + "}catch(e){}}"
            + "new MutationObserver(kill).observe(document.documentElement,{childList:true,subtree:true});kill();})();";
    private JSONObject apiLogin(String login, String pass) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL("https://id.thundr.com/api/users/login").openConnection();
        c.setRequestMethod("POST");
        c.setConnectTimeout(15000);
        c.setReadTimeout(20000);
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json");
        c.setRequestProperty("Accept", "application/json");
        c.setRequestProperty("User-Agent", USER_AGENT);
        JSONObject body = new JSONObject();
        body.put("email", login);
        body.put("password", pass);
        try (OutputStream os = c.getOutputStream()) {
            os.write(body.toString().getBytes(StandardCharsets.UTF_8));
        }
        int code = c.getResponseCode();
        InputStream is = code >= 400 ? c.getErrorStream() : c.getInputStream();
        java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while (is != null && (n = is.read(buf)) > 0) bos.write(buf, 0, n);
        if (code != 200) throw new RuntimeException("HTTP " + code);
        return new JSONObject(bos.toString("UTF-8"));
    }

    private void initWebView() {
        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setDatabaseEnabled(true);
        ws.setLoadWithOverviewMode(true);
        ws.setUseWideViewPort(true);
        ws.setSupportZoom(false);
        ws.setBuiltInZoomControls(false);
        ws.setAllowFileAccess(true);
        ws.setAllowContentAccess(true);
        ws.setMediaPlaybackRequiresUserGesture(false);
        ws.setUserAgentString(USER_AGENT);

        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                if (newProgress < 100) {
                    progressBar.setVisibility(View.VISIBLE);
                    progressBar.setProgress(newProgress);
                } else {
                    progressBar.setVisibility(View.GONE);
                }
            }

            // WebRTC: camera / microphone for video roulette
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                runOnUiThread(() -> {
                    if (hasMediaPermissions()) {
                        try {
                            request.grant(request.getResources());
                        } catch (Exception e) {
                            e.printStackTrace();
                        }
                    } else {
                        pendingWebPermission = request;
                        requestCameraAndAudioPermissions();
                    }
                });
            }

            @Override
            public boolean onConsoleMessage(ConsoleMessage m) {
                android.util.Log.d("ThundrWeb", m.message() + " @" + m.sourceId() + ":" + m.lineNumber());
                return true;
            }
        });

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                if (url != null && url.startsWith(ORIGIN) && !url.startsWith(SEED_URL)) {
                    view.evaluateJavascript(POPUP_KILLER_JS, null);
                }
                if (!seeded && url != null && url.startsWith(SEED_URL) && userJson != null) {
                    seeded = true;
                    String js = "(function(){try{"
                            + "localStorage.setItem('user'," + JSONObject.quote(userJson) + ");"
                            + "localStorage.setItem('settings'," + JSONObject.quote(settingsJson) + ");"
                            + "localStorage.setItem('notification_match_history_1_last_shown',new Date().toDateString());"
                            + "localStorage.setItem('downloadPopupDismissedAt',String(Date.now()));"
                            + "}catch(e){}})();";
                    view.evaluateJavascript(js, value -> view.loadUrl(ORIGIN));
                }
            }
        });
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.clearHistory();
        }
        super.onDestroy();
    }
}
