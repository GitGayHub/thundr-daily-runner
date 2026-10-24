package com.thundr.swapper;

import android.app.Activity;
import android.app.Dialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.View;
import android.view.Window;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;

public class MainActivity extends Activity {

    private static final String PREFS_NAME = "thundr_swapper_prefs";
    private static final String KEY_ACCOUNTS_JSON = "cached_accounts_json";
    private static final String KEY_CURRENT_USER = "current_active_username";

    private WebView webView;
    private ProgressBar progressBar;
    private TextView tvAppTitle;
    private Button btnOpenAccounts;

    private final List<AccountModel> accountsList = new ArrayList<>();
    private AccountAdapter accountAdapter;
    private AccountModel currentAccount = null;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        tvAppTitle = findViewById(R.id.tvAppTitle);
        btnOpenAccounts = findViewById(R.id.btnOpenAccounts);
        progressBar = findViewById(R.id.progressBar);
        webView = findViewById(R.id.webView);

        initWebView();
        loadAccountsData();

        btnOpenAccounts.setOnClickListener(v -> showAccountsDialog());

        // Auto-select first account or previously active account
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        String lastUser = prefs.getString(KEY_CURRENT_USER, null);

        AccountModel target = null;
        if (lastUser != null) {
            for (AccountModel a : accountsList) {
                if (a.username.equalsIgnoreCase(lastUser)) {
                    target = a;
                    break;
                }
            }
        }
        if (target == null && !accountsList.isEmpty()) {
            target = accountsList.get(0);
        }

        if (target != null) {
            switchToAccount(target, false);
        } else {
            webView.loadUrl("https://thundr.com");
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
        });

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                injectActiveSession();
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                injectActiveSession();
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return false;
            }
        });
    }

    private void injectActiveSession() {
        if (currentAccount == null) return;
        String userPayload = currentAccount.getUserPayloadJson();
        String js = "(function() {" +
                "  try {" +
                "    const u = " + userPayload + ";" +
                "    localStorage.setItem('user', JSON.stringify(u));" +
                "    sessionStorage.setItem('user', JSON.stringify(u));" +
                "  } catch(e) {}" +
                "})();";
        webView.evaluateJavascript(js, null);
    }

    private void loadAccountsData() {
        accountsList.clear();
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        String cachedJson = prefs.getString(KEY_ACCOUNTS_JSON, null);

        if (cachedJson != null && !cachedJson.trim().isEmpty()) {
            parseAccountsJson(cachedJson);
        }

        // Fallback to bundled assets/accounts.json if cache empty
        if (accountsList.isEmpty()) {
            String assetJson = loadAssetString("accounts.json");
            if (assetJson != null) {
                parseAccountsJson(assetJson);
            }
        }

        btnOpenAccounts.setText("⚡ Сменить (" + accountsList.size() + ")");
    }

    private void parseAccountsJson(String jsonStr) {
        try {
            JSONArray arr = new JSONArray(jsonStr);
            for (int i = 0; i < arr.length(); i++) {
                JSONObject obj = arr.getJSONObject(i);
                accountsList.add(AccountModel.fromJson(obj));
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private String loadAssetString(String filename) {
        try {
            InputStream is = getAssets().open(filename);
            BufferedReader reader = new BufferedReader(new InputStreamReader(is));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                sb.append(line);
            }
            reader.close();
            return sb.toString();
        } catch (Exception e) {
            return null;
        }
    }

    public void switchToAccount(AccountModel acc, boolean reload) {
        this.currentAccount = acc;

        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        prefs.edit().putString(KEY_CURRENT_USER, acc.username).apply();

        tvAppTitle.setText("👤 " + acc.username + " (" + acc.minutes + " мин | " + acc.balance + " 🪙)");
        Toast.makeText(this, "✅ Переключено на " + acc.username, Toast.LENGTH_SHORT).show();

        injectActiveSession();
        if (reload) {
            webView.loadUrl("https://thundr.com");
        } else {
            webView.loadUrl("https://thundr.com");
        }
    }

    private void showAccountsDialog() {
        Dialog dialog = new Dialog(this);
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE);
        dialog.setContentView(R.layout.dialog_accounts);
        dialog.getWindow().setLayout(
                (int) (getResources().getDisplayMetrics().widthPixels * 0.95),
                (int) (getResources().getDisplayMetrics().heightPixels * 0.85)
        );

        EditText etSearch = dialog.findViewById(R.id.etSearch);
        TextView tvTotalStats = dialog.findViewById(R.id.tvTotalStats);
        Button btnSync = dialog.findViewById(R.id.btnSyncGithub);
        Button btnClose = dialog.findViewById(R.id.btnCloseDialog);
        ListView lvAccounts = dialog.findViewById(R.id.lvAccounts);

        int totalCoins = 0;
        int totalMins = 0;
        for (AccountModel a : accountsList) {
            totalCoins += a.balance;
            totalMins += a.minutes;
        }
        tvTotalStats.setText("Всего: " + accountsList.size() + " акк. | ⏱️ " + totalMins + " мин | 💰 " + totalCoins + " монет");

        accountAdapter = new AccountAdapter(this, accountsList);
        lvAccounts.setAdapter(accountAdapter);

        etSearch.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            @Override
            public void onTextChanged(CharSequence s, int start, int count, int after) {
                accountAdapter.filter(s.toString());
            }
            @Override
            public void afterTextChanged(Editable s) {}
        });

        lvAccounts.setOnItemClickListener((parent, view, position, id) -> {
            AccountModel selected = accountAdapter.getItem(position);
            dialog.dismiss();
            switchToAccount(selected, true);
        });

        btnSync.setOnClickListener(v -> syncAccountsFromGithub(dialog, tvTotalStats));
        btnClose.setOnClickListener(v -> dialog.dismiss());

        dialog.show();
    }

    private void syncAccountsFromGithub(Dialog dialog, TextView tvStats) {
        Toast.makeText(this, "🔄 Загрузка актуальных данных с GitHub...", Toast.LENGTH_SHORT).show();
        Handler mainHandler = new Handler(Looper.getMainLooper());

        new Thread(() -> {
            String repo = "GitGayHub/thundr-daily-runner";
            String rawUrl = "https://raw.githubusercontent.com/" + repo + "/main/android/src/main/assets/accounts.json";

            try {
                URL url = new URL(rawUrl);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(10000);
                conn.setReadTimeout(10000);

                int responseCode = conn.getResponseCode();
                if (responseCode == 200) {
                    BufferedReader in = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                    StringBuilder response = new StringBuilder();
                    String line;
                    while ((line = in.readLine()) != null) {
                        response.append(line);
                    }
                    in.close();

                    String resultJson = response.toString();
                    mainHandler.post(() -> {
                        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
                        prefs.edit().putString(KEY_ACCOUNTS_JSON, resultJson).apply();
                        loadAccountsData();
                        if (accountAdapter != null) {
                            accountAdapter.updateData(accountsList);
                        }

                        int totalCoins = 0;
                        int totalMins = 0;
                        for (AccountModel a : accountsList) {
                            totalCoins += a.balance;
                            totalMins += a.minutes;
                        }
                        tvStats.setText("Всего: " + accountsList.size() + " акк. | ⏱️ " + totalMins + " мин | 💰 " + totalCoins + " монет");
                        Toast.makeText(MainActivity.this, "✅ Успешно обновлено " + accountsList.size() + " аккаунтов!", Toast.LENGTH_SHORT).show();
                    });
                } else {
                    mainHandler.post(() -> Toast.makeText(MainActivity.this, "⚠️ GitHub вернул статус: " + responseCode, Toast.LENGTH_SHORT).show());
                }
            } catch (Exception e) {
                mainHandler.post(() -> Toast.makeText(MainActivity.this, "⚠️ Ошибка синхронизации с GitHub: " + e.getMessage(), Toast.LENGTH_SHORT).show());
            }
        }).start();
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
