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
        int balance = obj.optInt("balance", 0);
        int minutes = obj.optInt("minutes", 0);
        int streak = obj.optInt("streak", 0);
        boolean isJackpot = obj.optBoolean("is_jackpot", false);
        boolean available = obj.optBoolean("available", false);
        String userId = obj.optString("user_id", "");
        String hashedToken = obj.optString("hashed_token", "");
        String email = obj.optString("email", username);
        String bookmarklet = obj.optString("bookmarklet", "");

        // If minutes not present, compute strict minutes:
        // < 100 -> 0 min
        // 100..449 -> balance / 100
        // >= 450 -> 450 = 10 min, 1000 = 30 min, 2100 = 60 min
        if (minutes == 0 && balance >= 100) {
            if (balance >= 450) {
                minutes = 10 + ((balance - 450) / 100);
            } else {
                minutes = balance / 100;
            }
        }

        return new AccountModel(username, password, balance, minutes, streak, isJackpot, available, userId, hashedToken, email, bookmarklet);
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
