package com.thundr.swapper;

import org.json.JSONObject;

public class AccountModel {
    public final String username;
    public final int balance;
    public final int minutes;
    public final int streak;
    public final boolean isJackpot;
    public final boolean available;
    public final String userId;
    public final String hashedToken;
    public final String email;

    public AccountModel(String username, int balance, int minutes, int streak,
                        boolean isJackpot, boolean available,
                        String userId, String hashedToken, String email) {
        this.username = username;
        this.balance = balance;
        this.minutes = minutes;
        this.streak = streak;
        this.isJackpot = isJackpot;
        this.available = available;
        this.userId = userId != null ? userId : "";
        this.hashedToken = hashedToken != null ? hashedToken : "";
        this.email = (email != null && !email.isEmpty()) ? email : username;
    }

    public static AccountModel fromJson(JSONObject obj) {
        String username = obj.optString("username", "account");
        int balance = obj.optInt("balance", 0);
        int minutes = obj.optInt("minutes", 0);
        int streak = obj.optInt("streak", 0);
        boolean isJackpot = obj.optBoolean("is_jackpot", false);
        boolean available = obj.optBoolean("available", false);
        String userId = obj.optString("user_id", "");
        String hashedToken = obj.optString("hashed_token", "");
        String email = obj.optString("email", username);

        // Fallback minute calculation if not present:
        if (minutes == 0 && balance >= 100) {
            if (balance >= 450) {
                minutes = 10 + ((balance - 450) / 100);
            } else {
                minutes = balance / 100;
            }
        }

        return new AccountModel(username, balance, minutes, streak, isJackpot, available, userId, hashedToken, email);
    }

    public String getUserPayloadJson() {
        try {
            JSONObject obj = new JSONObject();
            obj.put("id", userId);
            obj.put("hashed_token", hashedToken);
            obj.put("email", email);
            obj.put("username", username);
            return obj.toString();
        } catch (Exception e) {
            return "{}";
        }
    }
}
