package com.thundr.swapper;

import android.content.Context;
import android.graphics.Color;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.Button;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class AccountAdapter extends BaseAdapter {

    public interface OnAccountActionListener {
        void onInstantLogin(AccountModel account);
        void onAccountLongClick(AccountModel account);
    }

    private final Context context;
    private final OnAccountActionListener listener;
    private final List<AccountModel> allAccounts = new ArrayList<>();
    private final List<AccountModel> displayedAccounts = new ArrayList<>();

    private String currentQuery = "";
    private String currentFilterMode = "all";

    public AccountAdapter(Context context, List<AccountModel> accounts, OnAccountActionListener listener) {
        this.context = context;
        this.listener = listener;
        this.allAccounts.addAll(accounts);
        applyFilters();
    }

    public void updateData(List<AccountModel> newAccounts) {
        this.allAccounts.clear();
        this.allAccounts.addAll(newAccounts);
        applyFilters();
    }

    public void setFilterMode(String mode) {
        this.currentFilterMode = mode;
        applyFilters();
    }

    public String getFilterMode() {
        return currentFilterMode;
    }

    public void filter(String query) {
        this.currentQuery = query != null ? query.toLowerCase().trim() : "";
        applyFilters();
    }

    private void applyFilters() {
        displayedAccounts.clear();
        for (AccountModel a : allAccounts) {
            if (!currentQuery.isEmpty() && !a.username.toLowerCase().contains(currentQuery)) {
                continue;
            }
            if ("minutes".equals(currentFilterMode) && (a.minutes <= 0 || a.locallyUsed)) {
                continue;
            }
            if ("used".equals(currentFilterMode) && !(a.locallyUsed || a.gitVerifiedSpent || a.gitVerifiedKept)) {
                continue;
            }
            if ("jackpot".equals(currentFilterMode) && !a.isJackpot) {
                continue;
            }
            if ("ready".equals(currentFilterMode) && !a.available) {
                continue;
            }
            if ("min_1".equals(currentFilterMode) && a.minutes != 1) {
                continue;
            }
            if ("min_2".equals(currentFilterMode) && a.minutes != 2) {
                continue;
            }
            if ("min_3".equals(currentFilterMode) && a.minutes != 3) {
                continue;
            }
            if ("min_4".equals(currentFilterMode) && a.minutes != 4) {
                continue;
            }
            if ("min_10".equals(currentFilterMode) && a.minutes < 5) {
                continue;
            }
            if ("min_0".equals(currentFilterMode) && a.minutes != 0) {
                continue;
            }
            displayedAccounts.add(a);
        }

        Collections.sort(displayedAccounts, (a, b) -> {
            if ("used".equals(currentFilterMode)) {
                if (a.locallyUsed != b.locallyUsed) {
                    return a.locallyUsed ? -1 : 1;
                }
                return Long.compare(b.usedAtEpoch, a.usedAtEpoch);
            }
            // Locally used accounts (pending Git check) ALWAYS sink to the very bottom
            if (a.locallyUsed != b.locallyUsed) {
                return a.locallyUsed ? 1 : -1;
            }
            if (a.locallyUsed && b.locallyUsed) {
                return Long.compare(a.usedAtEpoch, b.usedAtEpoch);
            }
            // Sort unused / Git-verified accounts by minutes DESC, then untouched before spent, then balance DESC
            if (b.minutes != a.minutes) {
                return Integer.compare(b.minutes, a.minutes);
            }
            if (a.gitVerifiedSpent != b.gitVerifiedSpent) {
                return a.gitVerifiedSpent ? 1 : -1;
            }
            if (b.balance != a.balance) {
                return Integer.compare(b.balance, a.balance);
            }
            return a.username.compareToIgnoreCase(b.username);
        });

        notifyDataSetChanged();
    }

    @Override
    public int getCount() {
        return displayedAccounts.size();
    }

    @Override
    public AccountModel getItem(int position) {
        return displayedAccounts.get(position);
    }

    @Override
    public long getItemId(int position) {
        return position;
    }

    @Override
    public View getView(int position, View convertView, ViewGroup parent) {
        if (convertView == null) {
            convertView = LayoutInflater.from(context).inflate(R.layout.item_account, parent, false);
        }

        final AccountModel acc = getItem(position);

        TextView tvUsername = convertView.findViewById(R.id.itemUsername);
        TextView tvJackpotBadge = convertView.findViewById(R.id.itemJackpotBadge);
        TextView tvMinutesBadge = convertView.findViewById(R.id.itemMinutesBadge);
        TextView tvDetails = convertView.findViewById(R.id.itemDetails);
        TextView tvStatus = convertView.findViewById(R.id.itemStatus);
        Button btnInstantLogin = convertView.findViewById(R.id.btnInstantLogin);

        tvUsername.setText(acc.username);

        if (acc.isJackpot) {
            tvJackpotBadge.setVisibility(View.VISIBLE);
        } else {
            tvJackpotBadge.setVisibility(View.GONE);
        }

        if (acc.locallyUsed) {
            convertView.setAlpha(0.68f);
            int minsBefore = acc.minutesBeforeUse > 0 ? acc.minutesBeforeUse : acc.minutes;
            int balBefore = acc.balanceBeforeUse > 0 ? acc.balanceBeforeUse : acc.balance;
            tvMinutesBadge.setText("⏳ Юзали (было " + minsBefore + " мин)");
            tvMinutesBadge.setTextColor(Color.parseColor("#F59E0B"));
            tvDetails.setText("💰 Было " + balBefore + " 🪙 | 📉 Скорее всего потрачен");
            tvStatus.setText("⏳ Ждёт сверки с Git");
            tvStatus.setTextColor(Color.parseColor("#F59E0B"));
            btnInstantLogin.setText("🔄 Войти снова (акк внизу · ждёт Git)");
        } else if (acc.gitVerifiedSpent) {
            convertView.setAlpha(acc.minutes == 0 ? 0.75f : 0.92f);
            tvMinutesBadge.setText("⏱ " + acc.minutes + " мин");
            tvMinutesBadge.setTextColor(Color.parseColor("#38BDF8"));
            tvDetails.setText("💰 Сейчас " + acc.balance + " 🪙 (было " + acc.balanceBeforeUse + ") | 🔥 " + acc.streak + " дн.");
            tvStatus.setText("✅ Git подтвердил: потрачен (" + acc.minutes + " мин)");
            tvStatus.setTextColor(Color.parseColor("#38BDF8"));
            btnInstantLogin.setText("⚡ Войти сразу (Авторизация + Рулетка)");
        } else if (acc.gitVerifiedKept) {
            convertView.setAlpha(1.0f);
            tvMinutesBadge.setText("⏱ " + acc.minutes + " мин");
            tvMinutesBadge.setTextColor(Color.parseColor("#10B981"));
            tvDetails.setText("💰 " + acc.balance + " монет (Git проверил) | 🔥 " + acc.streak + " дн.");
            tvStatus.setText("✅ Git проверил: цел (" + acc.minutes + " мин)");
            tvStatus.setTextColor(Color.parseColor("#10B981"));
            btnInstantLogin.setText("⚡ Войти сразу (Авторизация + Рулетка)");
        } else {
            convertView.setAlpha(1.0f);
            tvMinutesBadge.setText("⏱ " + acc.minutes + " мин");
            tvMinutesBadge.setTextColor(Color.parseColor("#38BDF8"));

            String streakIcon = acc.isJackpot ? "🎰" : "🔥";
            tvDetails.setText("💰 " + acc.balance + " монет | " + streakIcon + " " + acc.streak + " дн.");

            if (acc.available) {
                tvStatus.setText("🎁 Готов к сбору");
                tvStatus.setTextColor(Color.parseColor("#F59E0B"));
            } else {
                tvStatus.setText("✅ Забран");
                tvStatus.setTextColor(Color.parseColor("#10B981"));
            }
            btnInstantLogin.setText("⚡ Войти сразу (Авторизация + Рулетка)");
        }

        convertView.setOnClickListener(v -> {
            if (listener != null) listener.onInstantLogin(acc);
        });

        convertView.setOnLongClickListener(v -> {
            if (listener != null) listener.onAccountLongClick(acc);
            return true;
        });

        btnInstantLogin.setOnClickListener(v -> {
            if (listener != null) listener.onInstantLogin(acc);
        });

        btnInstantLogin.setOnLongClickListener(v -> {
            if (listener != null) listener.onAccountLongClick(acc);
            return true;
        });

        return convertView;
    }
}
