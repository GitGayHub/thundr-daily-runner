package com.thundr.swapper;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.ProgressBar;
import android.widget.TextView;

public class BrowserActivity extends Activity {

    public static final String EXTRA_USERNAME = "extra_username";
    public static final String EXTRA_USER_ID = "extra_user_id";
    public static final String EXTRA_TOKEN = "extra_token";
    public static final String EXTRA_EMAIL = "extra_email";
    public static final String EXTRA_BALANCE = "extra_balance";
    public static final String EXTRA_MINUTES = "extra_minutes";

    private String username;
    private String userId;
    private String hashedToken;
    private String email;
    private int balance;
    private int minutes;

    private TextView tvBrowserTitle;
    private Button btnBack;
    private Button btnReload;
    private ProgressBar progressBar;
    private WebView webView;

    private boolean isReloadedWithSession = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Hardware acceleration for video roulette / WebRTC
        getWindow().setFlags(
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED
        );

        setContentView(R.layout.activity_browser);

        // Request runtime permissions for Camera and Audio (WebRTC video roulette)
        requestCameraAndAudioPermissions();

        // Read intent extras
        username = getIntent().getStringExtra(EXTRA_USERNAME);
        userId = getIntent().getStringExtra(EXTRA_USER_ID);
        hashedToken = getIntent().getStringExtra(EXTRA_TOKEN);
        email = getIntent().getStringExtra(EXTRA_EMAIL);
        balance = getIntent().getIntExtra(EXTRA_BALANCE, 0);
        minutes = getIntent().getIntExtra(EXTRA_MINUTES, 0);

        tvBrowserTitle = findViewById(R.id.tvBrowserAccountTitle);
        btnBack = findViewById(R.id.btnBackToAccounts);
        btnReload = findViewById(R.id.btnReloadPage);
        progressBar = findViewById(R.id.browserProgressBar);
        webView = findViewById(R.id.browserWebView);

        tvBrowserTitle.setText("👤 " + username + " (" + minutes + " мин | " + balance + " 🪙)");

        btnBack.setOnClickListener(v -> finish());
        btnReload.setOnClickListener(v -> {
            injectSession(webView);
            webView.reload();
        });

        initWebView();
    }

    private void requestCameraAndAudioPermissions() {
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED ||
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{
                Manifest.permission.CAMERA,
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.MODIFY_AUDIO_SETTINGS
            }, 101);
        }
    }

    private void initWebView() {
        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setDatabaseEnabled(true);
        ws.setLoadWithOverviewMode(true);
        ws.setUseWideViewPort(true);
        ws.setSupportZoom(true);
        ws.setBuiltInZoomControls(true);
        ws.setDisplayZoomControls(false);
        ws.setAllowFileAccess(true);
        ws.setAllowContentAccess(true);

        // CRITICAL FOR WEBRTC VIDEO ROULETTE: Allow autoplay without requiring user touch
        ws.setMediaPlaybackRequiresUserGesture(false);

        // Chrome User Agent (Mobile Chrome 124)
        ws.setUserAgentString("Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36");

        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                if (newProgress < 100) {
                    progressBar.setVisibility(View.VISIBLE);
                } else {
                    progressBar.setVisibility(View.GONE);
                }
            }

            // CRITICAL FOR WEBRTC VIDEO ROULETTE: Grant Camera and Audio permissions to WebRTC
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                runOnUiThread(() -> {
                    try {
                        request.grant(request.getResources());
                    } catch (Exception e) {
                        e.printStackTrace();
                    }
                });
            }
        });

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                injectSession(view);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                injectSession(view);
                if (!isReloadedWithSession && url != null && url.contains("thundr.com")) {
                    isReloadedWithSession = true;
                    view.evaluateJavascript(
                            "(function() {" +
                            "  if (!window.__thundr_swapped) {" +
                            "    window.__thundr_swapped = true;" +
                            "    location.reload();" +
                            "  }" +
                            "})();", null);
                }
            }
        });

        // Load starting URL
        webView.loadUrl("https://thundr.com");
    }

    private void injectSession(WebView view) {
        if (view == null || userId == null || hashedToken == null) return;

        String safeUsername = username != null ? username : "";
        String safeEmail = (email != null && !email.isEmpty()) ? email : safeUsername;

        String payload = "{\"id\":\"" + userId + "\",\"hashed_token\":\"" + hashedToken + "\",\"email\":\"" + safeEmail + "\",\"username\":\"" + safeUsername + "\"}";
        String js = "(function() {" +
                "  try {" +
                "    var u = " + payload + ";" +
                "    localStorage.setItem('user', JSON.stringify(u));" +
                "    sessionStorage.setItem('user', JSON.stringify(u));" +
                "  } catch(e) {}" +
                "})();";
        view.evaluateJavascript(js, null);
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
