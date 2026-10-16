# 🤖 Technical Agent Handbook (FOR_AGENT.md)

This document contains authoritative technical context, domain knowledge, API specifics, and architectural invariants for AI agents maintaining or extending `thundr-daily-runner`.

---

## 1. Domain & API Specifics (Reverse-Engineered from Thundr.com)

### 1.1 Authentication & Token Mechanics
- **Web UI Entry:** `https://thundr.com/signup` (contains both Sign Up and Sign In form: `input[placeholder='Username']`, `input[placeholder='Password']`, `button:has-text('Sign In')`).
- **Storage Location:** Upon successful authentication, user session is written to browser `localStorage['user']`.
- **JSON Structure of `localStorage['user']`:**
  ```json
  {
    "id": "ac1ad73b-dd54-4f73-b14e-b164a592eea4",
    "hashed_token": "qKih/zYuAkYELzSpDY+nEr/2ek1KuI9/X511SHd5KD8=",
    "email": "username",
    "created_at": 1792179990678,
    "onboarded": true
  }
  ```
- ⚠️ **CRITICAL URL Encoding Requirement:**
  `hashed_token` is a base64-encoded SHA-256 HMAC ending in `=` and frequently containing `+` and `/`.
  In Python, standard `urllib.parse.quote(token)` leaves `/` unencoded (`safe='/'` default).
  **You MUST use `urllib.parse.quote(token, safe='')`** (equivalent to JavaScript's `encodeURIComponent`).
  Failure to encode `+` converts it to a space on the server, producing a false `401 {"error":"unauthorized"}`!

### 1.2 Endpoints Specification
Base URL for all Coins & Crate operations is **`https://id.thundr.com`** (NOT `thundr.com`).

| Operation | Method | Full URL | Headers / Body |
|---|---|---|---|
| **Get Balance** | `GET` | `https://id.thundr.com/api/coins/balance/{user_id}?hashed_token={encoded_token}` | Headers: `User-Agent: Chrome/120` |
| **Get Crate Status** | `GET` | `https://id.thundr.com/api/coins/daily-crate/status/{user_id}?hashed_token={encoded_token}` | Headers: `User-Agent: Chrome/120` |
| **Claim Daily Crate** | `POST` | `https://id.thundr.com/api/coins/daily-crate/claim` | Headers: `Content-Type: application/json`<br>Body: `{"user_id": "...", "hashed_token": "..."}` |
| **Spend Boost** | `POST` | `https://id.thundr.com/api/coins/spend/boost` | Body: `{"user_id": "...", "hashed_token": "...", "boost": "mini"}` |

### 1.3 Crate Reset Timing & Lifecycle
- Daily reset occurs precisely at **`00:00:00 UTC`** every day (`next_reset_at`).
- Status response payload:
  ```json
  {
    "available": false,
    "coin_balance": 10,
    "last_claimed_on": "2026-10-16",
    "next_reset_at": "2026-10-17T00:00:00Z",
    "next_streak_bonus_at": 7,
    "ok": true,
    "server_now": "2026-10-16T20:03:33Z",
    "streak": 1,
    "streak_bonus": false
  }
  ```
- When crate is already claimed and POST is sent:
  - HTTP `409 Conflict`
  - Body: `{"error": "daily_crate_already_claimed", "ok": false}`

---

## 2. Architecture & Invariants

### 2.1 Hybrid Execution Paradigm
1. **API First:** Always attempt the lightweight HTTP API via `curl_cffi` (impersonate `chrome120`) with fallback to standard `requests`. This costs ~300ms and zero headless browser overhead.
2. **Browser On-Demand:** Only launch Playwright Chromium when:
   - Initial authentication without existing `hashed_token`.
   - API returns `401 Unauthorized` (token expired or invalidated).
3. **Session Persistence:** When browser retrieves a new `hashed_token`, immediately persist it to `AccountManager` so subsequent runs bypass the browser.

### 2.2 Finite State Machine Invariants
Defined in `thundr/state_machine.py`:
- `UNINITIALIZED` ➔ `CHECKING`: Fetches current crate status.
- `CHECKING` ➔ `COOLDOWN`: If `available == False`, terminates safely.
- `CHECKING` ➔ `READY`: If `available == True`.
- `READY` ➔ `CLAIMING`: Dispatches POST claim request.
- `CLAIMING` ➔ `VERIFYING`:
  - If POST returned 200, verifies balance increment or `available` change.
  - If POST suffered a network timeout (`ThundrNetworkError`), DOES NOT fail immediately! Instead, transitions to `VERIFYING` to check if the server processed the claim before dropping connection.
- Terminal States: `CLAIMED`, `COOLDOWN`, `FAILED`, `UNCERTAIN`.

### 2.3 Secrets Management & Zero-Leak Guarantee
- In logs and stdout: `mask_secret()` formats tokens as `qKih...KD8=`.
- `AccountRecord.to_dict(include_secrets=False)` is used when serializing public state.
- History files (`data/history.json`, `data/history.csv`) NEVER store passwords or session tokens.
- In CI/CD, multiple accounts can be passed via `THUNDR_ACCOUNTS_JSON` secret without touching disk.

---

## 3. Running & Extending Tests

Run all unit and mock tests:
```bash
python -m pytest tests/ -v
```

All 35 tests must pass without errors or warnings.
When adding new endpoints (e.g. boost claiming or store items), add corresponding tests in `tests/test_client.py` and `tests/test_state_machine.py`.
