package com.thundr.swapper;

import android.content.Context;
import android.graphics.Color;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

public class AccountAdapter extends BaseAdapter {
    private final Context context;
    private final List<AccountModel> allAccounts;
    private List<AccountModel> filteredAccounts;

    public AccountAdapter(Context context, List<AccountModel> accounts) {
        this.context = context;
        this.allAccounts = new ArrayList<>(accounts);
        this.filteredAccounts = new ArrayList<>(accounts);
    }

    public void filter(String query) {
        query = query.toLowerCase().trim();
        filteredAccounts.clear();
        if (query.isEmpty()) {
            filteredAccounts.addAll(allAccounts);
        } else {
            for (AccountModel acc : allAccounts) {
                if (acc.username.toLowerCase().contains(query)) {
                    filteredAccounts.add(acc);
                }
            }
        }
        notifyDataSetChanged();
    }

    public void updateData(List<AccountModel> newAccounts) {
        allAccounts.clear();
        allAccounts.addAll(newAccounts);
        filteredAccounts.clear();
        filteredAccounts.addAll(newAccounts);
        notifyDataSetChanged();
    }

    @Override
    public int getCount() {
        return filteredAccounts.size();
    }

    @Override
    public AccountModel getItem(int position) {
        return filteredAccounts.get(position);
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

        AccountModel acc = getItem(position);

        TextView tvUsername = convertView.findViewById(R.id.itemUsername);
        TextView tvMinutesBadge = convertView.findViewById(R.id.itemMinutesBadge);
        TextView tvDetails = convertView.findViewById(R.id.itemDetails);
        TextView tvStatus = convertView.findViewById(R.id.itemStatus);

        tvUsername.setText(acc.username);
        tvMinutesBadge.setText(acc.minutes + " мин");

        String icon = acc.isJackpot ? "🎰" : "🔥";
        String jackpotSuffix = acc.isJackpot ? " (ДЖЕКПОТ!)" : "";
        tvDetails.setText("💰 " + acc.balance + " монет | " + icon + " " + acc.streak + " дн." + jackpotSuffix);

        if (acc.available) {
            tvStatus.setText("⏳ Доступен");
            tvStatus.setTextColor(Color.parseColor("#F59E0B"));
        } else {
            tvStatus.setText("✅ Забран");
            tvStatus.setTextColor(Color.parseColor("#10B981"));
        }

        return convertView;
    }
}
