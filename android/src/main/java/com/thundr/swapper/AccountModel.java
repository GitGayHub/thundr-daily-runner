package com.thundr.swapper;

import org.json.JSONObject;

public class AccountModel {
    public final String username;
    public final String password;
    public final int balance;
    public final int minutes;
    public final int streak;
    public final boolean isJackpot;
    public final boolean available;
    public final String userId;
    public final String hashedToken;
    public final String email;
    public final String bookmarklet;

    // Local usage & Git verification tracking
    public boolean locallyUsed = false;
    public long usedAtEpoch = 0;
    public int balanceBeforeUse = 0;
    public int minutesBeforeUse = 0;
    public boolean gitVerifiedSpent = false;
    public boolean gitVerifiedKept = false;

    public AccountModel(String username, String password, int balance, int minutes,
                        int streak, boolean isJackpot, boolean available,
                        String userId, String hashedToken, String email,
                        String bookmarklet) {
        this.username = username != null ? username : "";
        this.password = password != null ? password : "";
        this.balance = balance;
        this.minutes = minutes;
        this.streak = streak;
        this.isJackpot = isJackpot;
        this.available = available;
        this.userId = userId != null ? userId : "";
        this.hashedToken = hashedToken != null ? hashedToken : "";
        this.email = (email != null && !email.isEmpty()) ? email : this.username;
        this.bookmarklet = bookmarklet != null && !bookmarklet.isEmpty()
                ? bookmarklet
                : buildBookmarklet(this.userId, this.hashedToken, this.email, this.username);
    }

    public static AccountModel fromJson(JSONObject obj) {
        String username = obj.optString("username", "account");
        String password = obj.optString("password", "");

        JSONObject statusObj = obj.optJSONObject("last_status");
        int balance = obj.optInt("balance", statusObj != null ? statusObj.optInt("coin_balance", 0) : 0);
        int minutes = obj.optInt("minutes", 0);
        int streak = obj.optInt("streak", statusObj != null ? statusObj.optInt("streak", 0) : 0);
        boolean isJackpot = obj.has("is_jackpot")
                ? obj.optBoolean("is_jackpot", false)
                : (streak > 0 && streak % 7 == 0);
        boolean available = obj.has("available")
                ? obj.optBoolean("available", false)
                : (statusObj != null && statusObj.optBoolean("available", false));

        JSONObject sessionObj = obj.optJSONObject("session");
        String userId = obj.optString("user_id", sessionObj != null ? sessionObj.optString("user_id", "") : "");
        String hashedToken = obj.optString("hashed_token", sessionObj != null ? sessionObj.optString("hashed_token", "") : "");
        String email = obj.optString("email", sessionObj != null ? sessionObj.optString("email", username) : username);
        String bookmarklet = obj.optString("bookmarklet", "");

        if (minutes == 0 && balance >= 100) {
            minutes = calculateBoostMinutes(balance);
        }

        return new AccountModel(username, password, balance, minutes, streak, isJackpot, available, userId, hashedToken, email, bookmarklet);
    }

    public static int calculateBoostMinutes(int coins) {
        if (coins < 100) return 0;
        if (coins >= 3000) {
            int base30s = (coins - 2000) / 1000;
            int rem = coins - base30s * 1000;
            return base30s * 30 + calculateBoostMinutes(rem);
        }
        int[] costs = {100, 450, 1000, 2100};
        int[] mins = {1, 10, 30, 60};
        int[] dp = new int[coins + 1];
        for (int c = 1; c <= coins; c++) {
            int best = dp[c - 1];
            for (int i = 0; i < costs.length; i++) {
                if (c >= costs[i]) {
                    int cand = dp[c - costs[i]] + mins[i];
                    if (cand > best) best = cand;
                }
            }
            dp[c] = best;
        }
        return dp[coins];
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

    public static String buildBookmarklet(String userId, String token, String email, String username) {
        String payload = "{\"id\":\"" + userId + "\",\"hashed_token\":\"" + token + "\",\"email\":\"" + email + "\",\"username\":\"" + username + "\"}";
        return "javascript:(function(){var u=" + payload + ";" +
                "try{localStorage.setItem('user',JSON.stringify(u));sessionStorage.setItem('user',JSON.stringify(u));alert('✅ Свап на: " + username + "');location.href='https://thundr.com';}catch(e){alert('Ошибка: '+e);}})();";
    }
}
