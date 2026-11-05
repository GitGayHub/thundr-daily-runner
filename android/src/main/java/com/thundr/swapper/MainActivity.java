package com.thundr.swapper;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
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

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;

public class MainActivity extends Activity implements AccountAdapter.OnAccountActionListener {

    private static final String PREFS_NAME = "thundr_swapper_prefs";
    private static final String KEY_ACCOUNTS_JSON = "cached_accounts_json";
    private static final String KEY_SYNC_EPOCH = "last_sync_epoch";
    private static final String KEY_SYNC_DISPLAY = "last_sync_display";
    private static final String CACHE_FILE_NAME = "accounts_cache.json";
    private static final String USED_STATE_FILE_NAME = "used_accounts_state.json";

    private static final String GITHUB_API_EXPORT_URL =
            "https://api.github.com/repos/GitGayHub/thundr-daily-runner/contents/data/accounts_export.json?ref=main";
    private static final String GITHUB_EXPORT_URL =
            "https://raw.githubusercontent.com/GitGayHub/thundr-daily-runner/main/data/accounts_export.json";
    private static final String GITHUB_REFS_EXPORT_URL =
            "https://raw.githubusercontent.com/GitGayHub/thundr-daily-runner/refs/heads/main/data/accounts_export.json";
    private static final String GITHUB_API_FALLBACK_URL =
            "https://api.github.com/repos/GitGayHub/thundr-daily-runner/contents/android/src/main/assets/accounts.json?ref=main";
    private static final String GITHUB_FALLBACK_URL =
            "https://raw.githubusercontent.com/GitGayHub/thundr-daily-runner/main/android/src/main/assets/accounts.json";

    private TextView tvAppTitle;
    private Button btnRefresh;
    private Button btnSettings;
    private TextView tvSyncStatus;
    private ProgressBar syncSpinner;
    private TextView tvStatCount;
    private TextView tvStatMinutes;
    private TextView tvStatCoins;
    private EditText etSearch;
    private Button btnSortOrder;

    private TextView chipAll;
    private TextView chipMinutes;
    private TextView chipUsed;
    private TextView chipJackpot;
    private TextView chipReady;

    private TextView chipMin1;
    private TextView chipMin2;
    private TextView chipMin3;
    private TextView chipMin4;
    private TextView chipMin10;
    private TextView chipMin0;

    private ListView lvAccounts;
    private TextView tvEmptyState;

    private final List<AccountModel> accountsList = new ArrayList<>();
    private AccountAdapter adapter;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private Runnable tickTimerRunnable;

    private long lastSyncEpoch = 0;
    private String lastSyncDisplay = "";
    private long lastSuccessfulFetchMs = 0;
    private final AtomicBoolean isSyncing = new AtomicBoolean(false);

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        bindViews();
        setupFilterChips();
        setupSearch();

        btnRefresh.setOnClickListener(v -> syncWithGithub(true));
        btnSettings.setOnClickListener(v -> showSettingsDialog());
        btnSortOrder.setOnClickListener(v -> toggleSortOrder());

        // Load cached accounts immediately (0.05s startup)
        loadCachedAccounts();

        // Setup adapter
        adapter = new AccountAdapter(this, accountsList, this);
        lvAccounts.setAdapter(adapter);
        updateSortButtonUI();

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
    protected void onResume() {
        super.onResume();
        updateSyncTimeUI();
        // Auto-sync when returning to the app or unlocking screen if previous sync failed or is > 60s old
        if (System.currentTimeMillis() - lastSuccessfulFetchMs > 60000) {
            syncWithGithub(false);
        }
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
        btnSettings = findViewById(R.id.btnSettings);
        tvSyncStatus = findViewById(R.id.tvSyncStatus);
        syncSpinner = findViewById(R.id.syncSpinner);
        tvStatCount = findViewById(R.id.tvStatCount);
        tvStatMinutes = findViewById(R.id.tvStatMinutes);
        tvStatCoins = findViewById(R.id.tvStatCoins);
        etSearch = findViewById(R.id.etSearch);
        btnSortOrder = findViewById(R.id.btnSortOrder);

        chipAll = findViewById(R.id.chipAll);
        chipMinutes = findViewById(R.id.chipMinutes);
        chipUsed = findViewById(R.id.chipUsed);
        chipJackpot = findViewById(R.id.chipJackpot);
        chipReady = findViewById(R.id.chipReady);

        chipMin1 = findViewById(R.id.chipMin1);
        chipMin2 = findViewById(R.id.chipMin2);
        chipMin3 = findViewById(R.id.chipMin3);
        chipMin4 = findViewById(R.id.chipMin4);
        chipMin10 = findViewById(R.id.chipMin10);
        chipMin0 = findViewById(R.id.chipMin0);

        lvAccounts = findViewById(R.id.lvAccounts);
        tvEmptyState = findViewById(R.id.tvEmptyState);
    }

    public static final String PREF_SIM_CYCLE = "sim_cycle_before_account";

    private void showSettingsDialog() {
        final SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);

        android.widget.LinearLayout box = new android.widget.LinearLayout(this);
        box.setOrientation(android.widget.LinearLayout.VERTICAL);
        int pad = (int) (20 * getResources().getDisplayMetrics().density);
        box.setPadding(pad, pad / 2, pad, 0);

        android.widget.CheckBox cb = new android.widget.CheckBox(this);
        cb.setText("🔄 Перезагружать SIM (0.5 сек) перед каждым новым аккаунтом — смена IP");
        cb.setTextSize(15);
        cb.setChecked(prefs.getBoolean(PREF_SIM_CYCLE, false));
        cb.setOnCheckedChangeListener((v, isChecked) -> prefs.edit().putBoolean(PREF_SIM_CYCLE, isChecked).apply());
        box.addView(cb);

        TextView hint = new TextView(this);
        hint.setText("Нужен ADB по сети (один раз с ПК: adb tcpip 5555) и разрешение отладки для приложения. Проверка — кнопка ниже.");
        hint.setTextSize(12);
        hint.setPadding(0, pad / 2, 0, pad / 2);
        box.addView(hint);

        new AlertDialog.Builder(this)
                .setTitle("⚙️ Настройки")
                .setView(box)
                .setPositiveButton("🔌 Проверить ADB", (d, w) -> testAdbAccess())
                .setNeutralButton("🧹 Сбросить юзаные", (d, w) -> clearAllUsedAccountsState())
                .setNegativeButton("Закрыть", null)
                .show();
    }

    /** Connects to the phone's own adbd (127.0.0.1:5555) and asks Android to authorize this app. */
    private void testAdbAccess() {
        final AlertDialog wait = new AlertDialog.Builder(this)
                .setTitle("🔌 ADB")
                .setMessage("Подключаюсь... Если на экране появится «Разрешить отладку по USB?» — отметь «Всегда разрешать» и нажми «Разрешить».")
                .setCancelable(false)
                .show();
        new Thread(() -> {
            String result;
            AdbShell adb = new AdbShell(this);
            try {
                adb.connect(60000);
                String id = adb.shell("id", 5000).trim();
                result = "✅ ADB работает.\n" + id + "\n\nПеред каждым аккаунтом SIM можно перезагружать.";
            } catch (java.net.ConnectException e) {
                result = "❌ Нет ADB по сети.\nНа ПК выполни один раз при подключенном кабеле:\nadb tcpip 5555";
            } catch (Exception e) {
                result = "❌ " + e;
            } finally {
                adb.close();
            }
            final String msg = result;
            runOnUiThread(() -> {
                wait.dismiss();
                new AlertDialog.Builder(this).setTitle("🔌 ADB").setMessage(msg).setPositiveButton("OK", null).show();
            });
        }).start();
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
        chipUsed.setOnClickListener(v -> selectFilter("used", chipUsed));
        chipJackpot.setOnClickListener(v -> selectFilter("jackpot", chipJackpot));
        chipReady.setOnClickListener(v -> selectFilter("ready", chipReady));

        chipMin1.setOnClickListener(v -> selectFilter("min_1", chipMin1));
        chipMin2.setOnClickListener(v -> selectFilter("min_2", chipMin2));
        chipMin3.setOnClickListener(v -> selectFilter("min_3", chipMin3));
        chipMin4.setOnClickListener(v -> selectFilter("min_4", chipMin4));
        chipMin10.setOnClickListener(v -> selectFilter("min_10", chipMin10));
        chipMin0.setOnClickListener(v -> selectFilter("min_0", chipMin0));
    }

    private void selectFilter(String mode, TextView selectedChip) {
        TextView[] allChips = {
                chipAll, chipMinutes, chipUsed, chipJackpot, chipReady,
                chipMin1, chipMin2, chipMin3, chipMin4, chipMin10, chipMin0
        };
        for (TextView chip : allChips) {
            if (chip == null) continue;
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
            lvAccounts.setSelection(0);
            checkEmptyState();
        }
    }

    private void toggleSortOrder() {
        if (adapter == null) return;
        boolean nextAsc = !adapter.isSortAscending();
        adapter.setSortAscending(nextAsc);
        updateSortButtonUI();
        lvAccounts.setSelection(0);
        String msg = nextAsc
                ? "⬆️ Сортировка: от меньшего к большему (100🪙 → 195🪙)"
                : "⬇️ Сортировка: от большего к меньшему (макс → 100🪙)";
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show();
    }

    private void updateSortButtonUI() {
        if (btnSortOrder == null || adapter == null) return;
        if (adapter.isSortAscending()) {
            btnSortOrder.setText("⬆️ От 100🪙");
            btnSortOrder.setTextColor(Color.parseColor("#38BDF8"));
        } else {
            btnSortOrder.setText("⬇️ От макс");
            btnSortOrder.setTextColor(Color.parseColor("#F59E0B"));
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
        int countMinutes = 0;
        int countUsed = 0;
        int countPendingGit = 0;
        int countJackpot = 0;
        int countReady = 0;

        int countMin1 = 0;
        int countMin2 = 0;
        int countMin3 = 0;
        int countMin4 = 0;
        int countMin10 = 0;
        int countMin0 = 0;

        for (AccountModel a : accountsList) {
            if (!a.locallyUsed) {
                totalCoins += a.balance;
                totalMinutes += a.minutes;
            }
            if (a.minutes > 0 && !a.locallyUsed) countMinutes++;
            if (a.locallyUsed || a.gitVerifiedSpent || a.gitVerifiedKept) countUsed++;
            if (a.locallyUsed) countPendingGit++;
            if (a.isJackpot && !a.locallyUsed) countJackpot++;
            if (a.available && !a.locallyUsed) countReady++;

            if (!a.locallyUsed) {
                if (a.minutes == 1) countMin1++;
                else if (a.minutes == 2) countMin2++;
                else if (a.minutes == 3) countMin3++;
                else if (a.minutes == 4) countMin4++;
                else if (a.minutes >= 5) countMin10++;
                else if (a.minutes == 0) countMin0++;
            }
        }

        if (countPendingGit > 0) {
            tvStatCount.setText((accountsList.size() - countPendingGit) + "/" + accountsList.size());
        } else {
            tvStatCount.setText(String.valueOf(accountsList.size()));
        }
        tvStatMinutes.setText(totalMinutes + " мин");
        tvStatCoins.setText(String.valueOf(totalCoins));

        chipAll.setText("Все (" + accountsList.size() + ")");
        chipMinutes.setText("⏱️ Есть минуты (" + countMinutes + ")");
        chipUsed.setText("⏳ Юзаные (" + countUsed + ")");
        chipJackpot.setText("🎰 Джекпот (" + countJackpot + ")");
        chipReady.setText("🎁 Сбор (" + countReady + ")");

        chipMin1.setText("⏱ 1 мин · 100🪙 (" + countMin1 + ")");
        chipMin2.setText("⏱ 2 мин · 200🪙 (" + countMin2 + ")");
        chipMin3.setText("⏱ 3 мин · 300🪙 (" + countMin3 + ")");
        chipMin4.setText("⏱ 4 мин · 400🪙 (" + countMin4 + ")");
        chipMin10.setText("🔥 10+ мин · 450+🪙 (" + countMin10 + ")");
        chipMin0.setText("💤 0 мин · <100🪙 (" + countMin0 + ")");
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

        int pendingGit = 0;
        for (AccountModel a : accountsList) {
            if (a.locallyUsed) pendingGit++;
        }
        String suffix = pendingGit > 0 ? " · ⏳ ждут Git: " + pendingGit : "";
        tvSyncStatus.setText("🔄 Git: " + displayTime + " (" + relative + ")" + suffix);
    }

    // --- Local Used-Account State & Git Verification ---

    private JSONObject readUsedStateJson() {
        File f = new File(getFilesDir(), USED_STATE_FILE_NAME);
        if (!f.exists()) return new JSONObject();
        try (FileInputStream fis = new FileInputStream(f);
             InputStreamReader isr = new InputStreamReader(fis, StandardCharsets.UTF_8)) {
            StringBuilder sb = new StringBuilder();
            char[] buf = new char[4096];
            int n;
            while ((n = isr.read(buf)) != -1) {
                sb.append(buf, 0, n);
            }
            return new JSONObject(sb.toString());
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    private void writeUsedStateJson(JSONObject stateObj) {
        if (stateObj == null) return;
        File f = new File(getFilesDir(), USED_STATE_FILE_NAME);
        try (FileOutputStream fos = new FileOutputStream(f)) {
            fos.write(stateObj.toString().getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private void markAccountUsedLocally(AccountModel acc) {
        long nowSec = System.currentTimeMillis() / 1000;
        int balBefore = acc.balanceBeforeUse > 0 ? acc.balanceBeforeUse : acc.balance;
        int minBefore = acc.minutesBeforeUse > 0 ? acc.minutesBeforeUse : acc.minutes;

        JSONObject stateObj = readUsedStateJson();
        try {
            JSONObject entry = new JSONObject();
            entry.put("used_at_epoch", nowSec);
            entry.put("git_epoch_at_use", lastSyncEpoch);
            entry.put("balance_before", balBefore);
            entry.put("minutes_before", minBefore);
            entry.put("state", "PENDING_GIT");
            stateObj.put(acc.username, entry);
            writeUsedStateJson(stateObj);
        } catch (Exception e) {
            e.printStackTrace();
        }

        acc.locallyUsed = true;
        acc.usedAtEpoch = nowSec;
        acc.balanceBeforeUse = balBefore;
        acc.minutesBeforeUse = minBefore;
        acc.gitVerifiedSpent = false;
        acc.gitVerifiedKept = false;

        if (adapter != null) {
            adapter.updateData(accountsList);
            checkEmptyState();
        }
        updateStatsUI();
        updateSyncTimeUI();
    }

    private void clearSingleAccountUsedState(AccountModel acc) {
        JSONObject stateObj = readUsedStateJson();
        stateObj.remove(acc.username);
        writeUsedStateJson(stateObj);

        acc.locallyUsed = false;
        acc.usedAtEpoch = 0;
        acc.balanceBeforeUse = 0;
        acc.minutesBeforeUse = 0;
        acc.gitVerifiedSpent = false;
        acc.gitVerifiedKept = false;

        if (adapter != null) {
            adapter.updateData(accountsList);
            checkEmptyState();
        }
        updateStatsUI();
        updateSyncTimeUI();
    }

    private void clearAllUsedAccountsState() {
        writeUsedStateJson(new JSONObject());
        for (AccountModel acc : accountsList) {
            acc.locallyUsed = false;
            acc.usedAtEpoch = 0;
            acc.balanceBeforeUse = 0;
            acc.minutesBeforeUse = 0;
            acc.gitVerifiedSpent = false;
            acc.gitVerifiedKept = false;
        }
        if (adapter != null) {
            adapter.updateData(accountsList);
            checkEmptyState();
        }
        updateStatsUI();
        updateSyncTimeUI();
        Toast.makeText(this, "🧹 Список юзаных аккаунтов очищен", Toast.LENGTH_SHORT).show();
    }

    private static class VerificationStats {
        int newlyVerifiedSpent = 0;
        int newlyVerifiedKept = 0;
        int stillPendingGit = 0;
    }

    /**
     * Reconciles local used-accounts state against incoming account balances and Git export timestamp.
     * An account marked PENDING_GIT stays at the bottom ("скорее всего потрачен") until Git runs a check
     * that either shows a lower balance/minutes (VERIFIED_SPENT) or has a newer updated_at_epoch than
     * when the account was used (VERIFIED_KEPT).
     */
    private VerificationStats applyUsedAccountsState(List<AccountModel> list, long currentGitEpoch, boolean isNetworkSync) {
        VerificationStats stats = new VerificationStats();
        JSONObject stateObj = readUsedStateJson();
        boolean dirty = false;

        for (AccountModel acc : list) {
            JSONObject entry = stateObj.optJSONObject(acc.username);
            if (entry == null) {
                continue;
            }
            String state = entry.optString("state", "PENDING_GIT");
            long usedAt = entry.optLong("used_at_epoch", 0);
            long gitEpochAtUse = entry.optLong("git_epoch_at_use", 0);
            int balBefore = entry.optInt("balance_before", acc.balance);
            int minBefore = entry.optInt("minutes_before", acc.minutes);

            acc.usedAtEpoch = usedAt;
            acc.balanceBeforeUse = balBefore;
            acc.minutesBeforeUse = minBefore;

            if ("PENDING_GIT".equals(state)) {
                boolean balanceDropped = (acc.balance < balBefore) || (acc.minutes < minBefore);
                boolean gitCheckedAfterUse = isNetworkSync
                        && currentGitEpoch > 0
                        && currentGitEpoch > usedAt
                        && currentGitEpoch > gitEpochAtUse;

                if (balanceDropped) {
                    acc.locallyUsed = false;
                    acc.gitVerifiedSpent = true;
                    acc.gitVerifiedKept = false;
                    stats.newlyVerifiedSpent++;
                    try {
                        entry.put("state", "VERIFIED_SPENT");
                        dirty = true;
                    } catch (Exception ignored) {}
                } else if (gitCheckedAfterUse) {
                    acc.locallyUsed = false;
                    acc.gitVerifiedSpent = false;
                    acc.gitVerifiedKept = true;
                    stats.newlyVerifiedKept++;
                    try {
                        entry.put("state", "VERIFIED_KEPT");
                        dirty = true;
                    } catch (Exception ignored) {}
                } else {
                    acc.locallyUsed = true;
                    acc.gitVerifiedSpent = false;
                    acc.gitVerifiedKept = false;
                    stats.stillPendingGit++;
                }
            } else if ("VERIFIED_SPENT".equals(state)) {
                if (acc.minutes > minBefore) {
                    // Account later farmed new boost minutes on a subsequent day
                    stateObj.remove(acc.username);
                    dirty = true;
                } else {
                    acc.locallyUsed = false;
                    acc.gitVerifiedSpent = true;
                    acc.gitVerifiedKept = false;
                }
            } else if ("VERIFIED_KEPT".equals(state)) {
                if (acc.balance < balBefore || acc.minutes < minBefore) {
                    acc.locallyUsed = false;
                    acc.gitVerifiedSpent = true;
                    acc.gitVerifiedKept = false;
                    try {
                        entry.put("state", "VERIFIED_SPENT");
                        dirty = true;
                    } catch (Exception ignored) {}
                } else {
                    acc.locallyUsed = false;
                    acc.gitVerifiedSpent = false;
                    acc.gitVerifiedKept = true;
                }
            }
        }

        if (dirty) {
            writeUsedStateJson(stateObj);
        }
        return stats;
    }

    private static class ParsedPayload {
        final List<AccountModel> accounts = new ArrayList<>();
        long updatedEpoch = 0;
        String updatedDisplay = "";
        int totalCoins = 0;
    }

    private ParsedPayload parseAccountsPayload(String jsonStr) {
        if (jsonStr == null || jsonStr.trim().isEmpty()) {
            return null;
        }
        ParsedPayload res = new ParsedPayload();
        try {
            String trimmed = jsonStr.trim();
            JSONArray arr = null;
            if (trimmed.startsWith("{")) {
                JSONObject root = new JSONObject(trimmed);
                res.updatedEpoch = root.optLong("updated_at_epoch", 0);
                res.updatedDisplay = root.optString("updated_at_display", "");
                arr = root.optJSONArray("accounts");
            } else if (trimmed.startsWith("[")) {
                arr = new JSONArray(trimmed);
            }
            if (arr != null) {
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject item = arr.optJSONObject(i);
                    if (item != null) {
                        AccountModel acc = AccountModel.fromJson(item);
                        res.accounts.add(acc);
                        res.totalCoins += acc.balance;
                    }
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
            return null;
        }
        return res.accounts.isEmpty() ? null : res;
    }

    private String readCacheFile() {
        File f = new File(getFilesDir(), CACHE_FILE_NAME);
        if (!f.exists()) return null;
        try (FileInputStream fis = new FileInputStream(f);
             InputStreamReader isr = new InputStreamReader(fis, StandardCharsets.UTF_8)) {
            StringBuilder sb = new StringBuilder();
            char[] buf = new char[8192];
            int n;
            while ((n = isr.read(buf)) != -1) {
                sb.append(buf, 0, n);
            }
            return sb.toString();
        } catch (Exception e) {
            return null;
        }
    }

    private void writeCacheFile(String jsonStr) {
        if (jsonStr == null || jsonStr.isEmpty()) return;
        File f = new File(getFilesDir(), CACHE_FILE_NAME);
        try (FileOutputStream fos = new FileOutputStream(f)) {
            fos.write(jsonStr.getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private void loadCachedAccounts() {
        accountsList.clear();
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        lastSyncEpoch = prefs.getLong(KEY_SYNC_EPOCH, 0);
        lastSyncDisplay = prefs.getString(KEY_SYNC_DISPLAY, "");

        String cachedJson = readCacheFile();
        if (cachedJson == null || cachedJson.trim().isEmpty()) {
            cachedJson = prefs.getString(KEY_ACCOUNTS_JSON, null);
        }
        ParsedPayload cachedPayload = parseAccountsPayload(cachedJson);

        String assetJson = loadAssetString("accounts.json");
        ParsedPayload assetPayload = parseAccountsPayload(assetJson);

        ParsedPayload chosen = cachedPayload;
        if (assetPayload != null) {
            if (chosen == null
                    || assetPayload.accounts.size() > chosen.accounts.size()
                    || assetPayload.updatedEpoch > chosen.updatedEpoch
                    || (assetPayload.updatedEpoch == chosen.updatedEpoch && assetPayload.totalCoins > chosen.totalCoins)) {
                chosen = assetPayload;
                writeCacheFile(assetJson);
                prefs.edit()
                        .putLong(KEY_SYNC_EPOCH, chosen.updatedEpoch)
                        .putString(KEY_SYNC_DISPLAY, chosen.updatedDisplay)
                        .apply();
            }
        }

        if (chosen != null) {
            if (chosen.updatedEpoch > 0) {
                lastSyncEpoch = chosen.updatedEpoch;
            }
            if (chosen.updatedDisplay != null && !chosen.updatedDisplay.isEmpty()) {
                lastSyncDisplay = chosen.updatedDisplay;
            }
            applyUsedAccountsState(chosen.accounts, lastSyncEpoch, false);
            accountsList.addAll(chosen.accounts);
        }
    }

    private ParsedPayload fetchBestPayloadFromGithub() {
        long ts = System.currentTimeMillis();
        String[][] endpoints = new String[][]{
                {GITHUB_API_EXPORT_URL + "&t=" + ts, "application/vnd.github.raw+json"},
                {GITHUB_EXPORT_URL + "?t=" + ts, null},
                {GITHUB_REFS_EXPORT_URL + "?t=" + ts, null},
                {GITHUB_API_FALLBACK_URL + "&t=" + ts, "application/vnd.github.raw+json"},
                {GITHUB_FALLBACK_URL + "?t=" + ts, null}
        };

        for (int attempt = 0; attempt < 2; attempt++) {
            for (String[] ep : endpoints) {
                String json = fetchUrl(ep[0], ep[1]);
                if (json != null && !json.trim().isEmpty()) {
                    ParsedPayload parsed = parseAccountsPayload(json);
                    if (parsed != null && !parsed.accounts.isEmpty()) {
                        writeCacheFile(json);
                        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
                        prefs.edit()
                                .putString(KEY_ACCOUNTS_JSON, json)
                                .putLong(KEY_SYNC_EPOCH, parsed.updatedEpoch)
                                .putString(KEY_SYNC_DISPLAY, parsed.updatedDisplay)
                                .apply();
                        return parsed;
                    }
                }
            }
            if (attempt == 0) {
                try {
                    Thread.sleep(1200);
                } catch (InterruptedException ignored) {}
            }
        }
        return null;
    }

    public void syncWithGithub(boolean isUserTriggered) {
        if (!isUserTriggered && !isSyncing.compareAndSet(false, true)) {
            return;
        }
        isSyncing.set(true);
        syncSpinner.setVisibility(View.VISIBLE);
        tvSyncStatus.setText("🔄 GitHub: сверка с базой Git...");

        new Thread(() -> {
            long nowEpoch = System.currentTimeMillis() / 1000;
            final ParsedPayload parsed = fetchBestPayloadFromGithub();
            isSyncing.set(false);

            mainHandler.post(() -> {
                syncSpinner.setVisibility(View.GONE);

                if (parsed != null && !parsed.accounts.isEmpty()) {
                    lastSuccessfulFetchMs = System.currentTimeMillis();

                    if (parsed.updatedEpoch > 0) {
                        lastSyncEpoch = parsed.updatedEpoch;
                    } else if (lastSyncEpoch == 0 || lastSyncEpoch < nowEpoch - 86400) {
                        lastSyncEpoch = nowEpoch;
                    }
                    if (parsed.updatedDisplay != null && !parsed.updatedDisplay.isEmpty()) {
                        lastSyncDisplay = parsed.updatedDisplay;
                    } else if (lastSyncDisplay == null || lastSyncDisplay.isEmpty()) {
                        lastSyncDisplay = new SimpleDateFormat("dd.MM HH:mm", Locale.getDefault()).format(new Date());
                    }

                    VerificationStats vStats = applyUsedAccountsState(parsed.accounts, parsed.updatedEpoch, true);

                    accountsList.clear();
                    accountsList.addAll(parsed.accounts);

                    if (adapter != null) {
                        adapter.updateData(accountsList);
                        if (isUserTriggered && adapter.getCount() == 0 && !accountsList.isEmpty()) {
                            etSearch.setText("");
                            selectFilter("all", chipAll);
                        }
                    }
                    updateStatsUI();
                    updateSyncTimeUI();
                    checkEmptyState();

                    if (isUserTriggered) {
                        int totalVerifiedNow = vStats.newlyVerifiedSpent + vStats.newlyVerifiedKept;
                        String msg;
                        if (totalVerifiedNow > 0) {
                            msg = "✅ Git проверил " + totalVerifiedNow + " акк. (потрачено: "
                                    + vStats.newlyVerifiedSpent + ", целы: " + vStats.newlyVerifiedKept + ")";
                            if (vStats.stillPendingGit > 0) {
                                msg += " · ⏳ ждут Git: " + vStats.stillPendingGit;
                            }
                        } else if (vStats.stillPendingGit > 0) {
                            msg = "✅ База Git загружена (" + accountsList.size() + " акк).\n⏳ "
                                    + vStats.stillPendingGit + " юзаных акк. внизу (ждут следующей проверки Git)";
                        } else {
                            msg = "✅ Обновлено с GitHub: " + accountsList.size() + " аккаунтов!";
                        }
                        Toast.makeText(MainActivity.this, msg, Toast.LENGTH_LONG).show();
                    }
                } else {
                    updateSyncTimeUI();
                    if (isUserTriggered) {
                        Toast.makeText(MainActivity.this, "⚠️ Не удалось подключиться к GitHub. Показаны сохранённые данные (" + accountsList.size() + " акк.).", Toast.LENGTH_LONG).show();
                    }
                }
            });
        }).start();
    }

    private String fetchUrl(String urlString, String acceptHeader) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(urlString);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(12000);
            conn.setReadTimeout(15000);
            conn.setUseCaches(false);
            conn.setRequestProperty("User-Agent", "ThundrSwapperApp/2.0");
            conn.setRequestProperty("Cache-Control", "no-cache, no-store, must-revalidate");
            conn.setRequestProperty("Pragma", "no-cache");
            if (acceptHeader != null) {
                conn.setRequestProperty("Accept", acceptHeader);
            }

            int code = conn.getResponseCode();
            if (code == 200) {
                InputStreamReader isr = new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8);
                StringBuilder sb = new StringBuilder(327680);
                char[] buf = new char[8192];
                int n;
                while ((n = isr.read(buf)) != -1) {
                    sb.append(buf, 0, n);
                }
                isr.close();
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
            InputStreamReader isr = new InputStreamReader(is, StandardCharsets.UTF_8);
            StringBuilder sb = new StringBuilder(327680);
            char[] buf = new char[8192];
            int n;
            while ((n = isr.read(buf)) != -1) {
                sb.append(buf, 0, n);
            }
            isr.close();
            return sb.toString();
        } catch (Exception e) {
            return null;
        }
    }

    // --- OnAccountActionListener implementations ---

    @Override
    public void onInstantLogin(AccountModel acc) {
        // Immediately mark as locally used (pending Git check) and sink to the bottom of the list
        markAccountUsedLocally(acc);

        Intent intent = new Intent(this, BrowserActivity.class);
        intent.putExtra(BrowserActivity.EXTRA_USERNAME, acc.username);
        intent.putExtra(BrowserActivity.EXTRA_USER_ID, acc.userId);
        intent.putExtra(BrowserActivity.EXTRA_TOKEN, acc.hashedToken);
        intent.putExtra(BrowserActivity.EXTRA_EMAIL, acc.username);
        intent.putExtra(BrowserActivity.EXTRA_PASSWORD, acc.password);
        intent.putExtra(BrowserActivity.EXTRA_BALANCE, acc.balance);
        intent.putExtra(BrowserActivity.EXTRA_MINUTES, acc.minutes);
        intent.putExtra(BrowserActivity.EXTRA_BOOKMARKLET, acc.getUserPayloadJson());
        startActivity(intent);
    }

    @Override
    public void onAccountLongClick(AccountModel acc) {
        if (acc.locallyUsed || acc.gitVerifiedSpent || acc.gitVerifiedKept) {
            new AlertDialog.Builder(this)
                    .setTitle(acc.username)
                    .setMessage("Снять отметку «Юзаный» и вернуть аккаунт обратно в общий список?")
                    .setPositiveButton("🔄 Вернуть в список", (d, w) -> {
                        clearSingleAccountUsedState(acc);
                        Toast.makeText(this, "✅ " + acc.username + " возвращён в список", Toast.LENGTH_SHORT).show();
                    })
                    .setNegativeButton("Отмена", null)
                    .show();
        } else {
            new AlertDialog.Builder(this)
                    .setTitle(acc.username)
                    .setMessage("Отметить аккаунт как использованный (опустить вниз до сверки с Git)?")
                    .setPositiveButton("⏳ Вниз (юзаный)", (d, w) -> {
                        markAccountUsedLocally(acc);
                        Toast.makeText(this, "⏳ " + acc.username + " опущен вниз (ждёт сверки с Git)", Toast.LENGTH_SHORT).show();
                    })
                    .setNegativeButton("Отмена", null)
                    .show();
        }
    }
}
