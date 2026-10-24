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
import java.util.List;

public class AccountAdapter extends BaseAdapter {

    public interface OnAccountActionListener {
        void onInstantLogin(AccountModel account);
        void onOpenInChrome(AccountModel account);
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
            if ("minutes".equals(currentFilterMode) && a.minutes <= 0) {
                continue;
            }
            if ("jackpot".equals(currentFilterMode) && !a.isJackpot) {
                continue;
            }
            if ("ready".equals(currentFilterMode) && !a.available) {
                continue;
            }
            displayedAccounts.add(a);
        }
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
        Button btnOpenChrome = convertView.findViewById(R.id.btnOpenChrome);

        tvUsername.setText(acc.username);
        tvMinutesBadge.setText("⏱ " + acc.minutes + " мин");

        if (acc.isJackpot) {
            tvJackpotBadge.setVisibility(View.VISIBLE);
        } else {
            tvJackpotBadge.setVisibility(View.GONE);
        }

        String streakIcon = acc.isJackpot ? "🎰" : "🔥";
        tvDetails.setText("💰 " + acc.balance + " монет | " + streakIcon + " " + acc.streak + " дн.");

        if (acc.available) {
            tvStatus.setText("🎁 Готов к сбору");
            tvStatus.setTextColor(Color.parseColor("#F59E0B"));
        } else {
            tvStatus.setText("✅ Забран");
            tvStatus.setTextColor(Color.parseColor("#10B981"));
        }

        convertView.setOnClickListener(v -> {
            if (listener != null) listener.onInstantLogin(acc);
        });

        btnInstantLogin.setOnClickListener(v -> {
            if (listener != null) listener.onInstantLogin(acc);
        });

        btnOpenChrome.setOnClickListener(v -> {
            if (listener != null) listener.onOpenInChrome(acc);
        });

        return convertView;
    }
}
