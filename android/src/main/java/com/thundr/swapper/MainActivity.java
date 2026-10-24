package com.thundr.swapper;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.View;
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
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

public class MainActivity extends Activity implements AccountAdapter.OnAccountActionListener {

    private static final String PREFS_NAME = "thundr_swapper_prefs";
    private static final String KEY_ACCOUNTS_JSON = "cached_accounts_json";
    private static final String KEY_SYNC_EPOCH = "last_sync_epoch";
    private static final String KEY_SYNC_DISPLAY = "last_sync_display";

    private static final String GITHUB_EXPORT_URL =
            "https://raw.githubusercontent.com/GitGayHub/thundr-daily-runner/main/data/accounts_export.json";
    private static final String GITHUB_FALLBACK_URL =
            "https://raw.githubusercontent.com/GitGayHub/thundr-daily-runner/main/android/src/main/assets/accounts.json";

    private TextView tvAppTitle;
    private Button btnRefresh;
    private TextView tvSyncStatus;
    private ProgressBar syncSpinner;
    private TextView tvStatCount;
    private TextView tvStatMinutes;
    private TextView tvStatCoins;
    private EditText etSearch;

    private TextView chipAll;
    private TextView chipMinutes;
    private TextView chipJackpot;
    private TextView chipReady;

    private ListView lvAccounts;
    private TextView tvEmptyState;

    private final List<AccountModel> accountsList = new ArrayList<>();
    private AccountAdapter adapter;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private Runnable tickTimerRunnable;

    private long lastSyncEpoch = 0;
    private String lastSyncDisplay = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        bindViews();
        setupFilterChips();
        setupSearch();

        btnRefresh.setOnClickListener(v -> syncWithGithub(true));

        // Load cached accounts immediately (0.05s startup)
        loadCachedAccounts();

        // Setup adapter
        adapter = new AccountAdapter(this, accountsList, this);
        lvAccounts.setAdapter(adapter);

        updateStatsUI();
        updateSyncTimeUI();

        // Background auto-sync from GitHub on app startup
        syncWithGithub(false);

        // Periodic ticker to refresh "X мин назад" every 30 seconds
        tickTimerRunnable = new Runnable() {
            @Override
            public void run() {
                updateSyncTimeUI();
                mainHandler.postDelayed(this, 30000);
            }
        };
        mainHandler.postDelayed(tickTimerRunnable, 30000);
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (tickTimerRunnable != null) {
            mainHandler.removeCallbacks(tickTimerRunnable);
        }
    }

    private void bindViews() {
        tvAppTitle = findViewById(R.id.tvAppTitle);
        btnRefresh = findViewById(R.id.btnRefresh);
        tvSyncStatus = findViewById(R.id.tvSyncStatus);
        syncSpinner = findViewById(R.id.syncSpinner);
        tvStatCount = findViewById(R.id.tvStatCount);
        tvStatMinutes = findViewById(R.id.tvStatMinutes);
        tvStatCoins = findViewById(R.id.tvStatCoins);
        etSearch = findViewById(R.id.etSearch);

        chipAll = findViewById(R.id.chipAll);
        chipMinutes = findViewById(R.id.chipMinutes);
        chipJackpot = findViewById(R.id.chipJackpot);
        chipReady = findViewById(R.id.chipReady);

        lvAccounts = findViewById(R.id.lvAccounts);
        tvEmptyState = findViewById(R.id.tvEmptyState);
    }

    private void setupSearch() {
        etSearch.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence s, int start, int count, int after) {}

            @Override
            public void onTextChanged(CharSequence s, int start, int count, int after) {
                if (adapter != null) {
                    adapter.filter(s.toString());
                    checkEmptyState();
                }
            }

            @Override
            public void afterTextChanged(Editable s) {}
        });
    }

    private void setupFilterChips() {
        chipAll.setOnClickListener(v -> selectFilter("all", chipAll));
        chipMinutes.setOnClickListener(v -> selectFilter("minutes", chipMinutes));
        chipJackpot.setOnClickListener(v -> selectFilter("jackpot", chipJackpot));
        chipReady.setOnClickListener(v -> selectFilter("ready", chipReady));
    }

    private void selectFilter(String mode, TextView selectedChip) {
        TextView[] allChips = {chipAll, chipMinutes, chipJackpot, chipReady};
        for (TextView chip : allChips) {
            if (chip == selectedChip) {
                chip.setBackgroundResource(R.drawable.chip_selected);
                chip.setTextColor(Color.parseColor("#000000"));
            } else {
                chip.setBackgroundResource(R.drawable.chip_unselected);
                chip.setTextColor(Color.parseColor("#94A3B8"));
            }
        }
        if (adapter != null) {
            adapter.setFilterMode(mode);
            checkEmptyState();
        }
    }

    private void checkEmptyState() {
        if (adapter != null) {
            if (adapter.getCount() == 0) {
                tvEmptyState.setVisibility(View.VISIBLE);
            } else {
                tvEmptyState.setVisibility(View.GONE);
            }
        }
    }

    private void updateStatsUI() {
        int totalCoins = 0;
        int totalMinutes = 0;
        for (AccountModel a : accountsList) {
            totalCoins += a.balance;
            totalMinutes += a.minutes;
        }

        tvStatCount.setText(String.valueOf(accountsList.size()));
        tvStatMinutes.setText(totalMinutes + " мин");
        tvStatCoins.setText(String.valueOf(totalCoins));
        chipAll.setText("Все (" + accountsList.size() + ")");
    }

    private void updateSyncTimeUI() {
        if (lastSyncEpoch <= 0) {
            tvSyncStatus.setText("🔄 GitHub: нет синхронизации");
            return;
        }

        long nowSec = System.currentTimeMillis() / 1000;
        long diffSec = nowSec - lastSyncEpoch;
        String relative;

        if (diffSec < 45) {
            relative = "только что";
        } else if (diffSec < 3600) {
            long mins = diffSec / 60;
            relative = mins + " мин назад";
        } else if (diffSec < 86400) {
            long hours = diffSec / 3600;
            relative = hours + " ч назад";
        } else {
            long days = diffSec / 86400;
            relative = days + " дн назад";
        }

        String displayTime = (lastSyncDisplay != null && !lastSyncDisplay.isEmpty())
                ? lastSyncDisplay
                : new SimpleDateFormat("dd.MM HH:mm", Locale.getDefault()).format(new Date(lastSyncEpoch * 1000));

        tvSyncStatus.setText("🔄 GitHub: " + displayTime + " (" + relative + ")");
    }

    private void loadCachedAccounts() {
        accountsList.clear();
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        lastSyncEpoch = prefs.getLong(KEY_SYNC_EPOCH, 0);
        lastSyncDisplay = prefs.getString(KEY_SYNC_DISPLAY, "");

        String cachedJson = prefs.getString(KEY_ACCOUNTS_JSON, null);
        if (cachedJson != null && !cachedJson.trim().isEmpty()) {
            parseAccountsJson(cachedJson);
        }

        // Fallback to bundled asset if cache empty
        if (accountsList.isEmpty()) {
            String assetJson = loadAssetString("accounts.json");
            if (assetJson != null) {
                parseAccountsJson(assetJson);
            }
        }
    }

    private void parseAccountsJson(String jsonStr) {
        accountsList.clear();
        try {
            String trimmed = jsonStr.trim();
            if (trimmed.startsWith("{")) {
                JSONObject root = new JSONObject(trimmed);
                if (root.has("updated_at_epoch")) {
                    lastSyncEpoch = root.optLong("updated_at_epoch", lastSyncEpoch);
                }
                if (root.has("updated_at_display")) {
                    lastSyncDisplay = root.optString("updated_at_display", lastSyncDisplay);
                }
                JSONArray arr = root.optJSONArray("accounts");
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        accountsList.add(AccountModel.fromJson(arr.getJSONObject(i)));
                    }
                }
            } else if (trimmed.startsWith("[")) {
                JSONArray arr = new JSONArray(trimmed);
                for (int i = 0; i < arr.length(); i++) {
                    accountsList.add(AccountModel.fromJson(arr.getJSONObject(i)));
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    public void syncWithGithub(boolean isUserTriggered) {
        syncSpinner.setVisibility(View.VISIBLE);
        tvSyncStatus.setText("🔄 GitHub: загрузка актуальных данных...");

        new Thread(() -> {
            String fetchedJson = null;
            long nowEpoch = System.currentTimeMillis() / 1000;

            // Try 1: accounts_export.json
            fetchedJson = fetchUrl(GITHUB_EXPORT_URL + "?t=" + System.currentTimeMillis());

            // Try 2: fallback to assets/accounts.json
            if (fetchedJson == null) {
                fetchedJson = fetchUrl(GITHUB_FALLBACK_URL + "?t=" + System.currentTimeMillis());
            }

            final String finalJson = fetchedJson;

            mainHandler.post(() -> {
                syncSpinner.setVisibility(View.GONE);

                if (finalJson != null && !finalJson.trim().isEmpty()) {
                    parseAccountsJson(finalJson);

                    if (lastSyncEpoch == 0 || lastSyncEpoch < nowEpoch - 86400) {
                        lastSyncEpoch = nowEpoch;
                    }
                    if (lastSyncDisplay == null || lastSyncDisplay.isEmpty()) {
                        lastSyncDisplay = new SimpleDateFormat("dd.MM HH:mm", Locale.getDefault()).format(new Date());
                    }

                    // Save to SharedPreferences
                    SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
                    prefs.edit()
                            .putString(KEY_ACCOUNTS_JSON, finalJson)
                            .putLong(KEY_SYNC_EPOCH, lastSyncEpoch)
                            .putString(KEY_SYNC_DISPLAY, lastSyncDisplay)
                            .apply();

                    if (adapter != null) {
                        adapter.updateData(accountsList);
                    }
                    updateStatsUI();
                    updateSyncTimeUI();
                    checkEmptyState();

                    if (isUserTriggered) {
                        Toast.makeText(MainActivity.this, "✅ Обновлено с GitHub: " + accountsList.size() + " аккаунтов!", Toast.LENGTH_SHORT).show();
                    }
                } else {
                    updateSyncTimeUI();
                    if (isUserTriggered) {
                        Toast.makeText(MainActivity.this, "⚠️ Не удалось подключиться к GitHub. Показаны сохранённые данные.", Toast.LENGTH_LONG).show();
                    }
                }
            });
        }).start();
    }

    private String fetchUrl(String urlString) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(urlString);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(8000);
            conn.setUseCaches(false);
            conn.setRequestProperty("User-Agent", "ThundrSwapperApp/1.0");

            int code = conn.getResponseCode();
            if (code == 200) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) {
                    sb.append(line);
                }
                reader.close();
                return sb.toString();
            }
        } catch (Exception e) {
            e.printStackTrace();
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
        return null;
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

    // --- OnAccountActionListener implementations ---

    @Override
    public void onOpenChrome(AccountModel acc) {
        copyToClipboard(acc.bookmarklet, "ThundrSession");

        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse("https://thundr.com"));
        intent.setPackage("com.android.chrome");
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);

        try {
            startActivity(intent);
            Toast.makeText(this, "🚀 Chrome запущен для " + acc.username + "!\n📋 Сессия скопирована в буфер обмена.", Toast.LENGTH_LONG).show();
        } catch (ActivityNotFoundException e) {
            // Fallback if Chrome package is not present
            try {
                Intent fallback = new Intent(Intent.ACTION_VIEW, Uri.parse("https://thundr.com"));
                fallback.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(fallback);
                Toast.makeText(this, "🌐 Браузер открыт для " + acc.username + "!\n📋 Сессия скопирована в буфер.", Toast.LENGTH_LONG).show();
            } catch (Exception ex) {
                Toast.makeText(this, "⚠️ Не удалось открыть браузер: " + ex.getMessage(), Toast.LENGTH_SHORT).show();
            }
        }
    }

    @Override
    public void onOpenIncognito(AccountModel acc) {
        copyToClipboard(acc.bookmarklet, "ThundrSession");

        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse("https://thundr.com"));
        intent.setPackage("com.android.chrome");
        intent.putExtra("com.google.android.apps.chrome.EXTRA_OPEN_NEW_INCOGNITO_TAB", true);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);

        try {
            startActivity(intent);
            Toast.makeText(this, "🕶️ Chrome Инкогнито (отдельный профиль) запущен для " + acc.username + "!\n📋 Сессия в буфере.", Toast.LENGTH_LONG).show();
        } catch (ActivityNotFoundException e) {
            onOpenChrome(acc);
        }
    }

    @Override
    public void onCopyScript(AccountModel acc) {
        copyToClipboard(acc.bookmarklet, "ThundrSession");
        Toast.makeText(this, "📋 Скрипт сессии для " + acc.username + " скопирован!\nВставьте в Chrome или используйте закладку.", Toast.LENGTH_LONG).show();
    }

    private void copyToClipboard(String text, String label) {
        ClipboardManager clipboard = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        ClipData clip = ClipData.newPlainText(label, text);
        clipboard.setPrimaryClip(clip);
    }
}
