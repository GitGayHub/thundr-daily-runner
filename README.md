# ⚡ Thundr Daily Rewards Runner v2.0

> Высоконадежная модульная система для автоматического получения ежедневных наград ([Thundr](https://thundr.com/)), отслеживания баланса монет, истории серий (streaks) и отправки детальных отчетов в Telegram.

Система спроектирована для работы в режиме 24/7 как в бесплатных **GitHub Actions**, так и локально на Windows, Linux или macOS.

---

## 🌟 Ключевые возможности

- 🚀 **Гибридная архитектура клиента (API + Headless Browser):**
  - **Быстрый API-путь:** прямые защищенные HTTP-запросы через `curl_cffi` (с TLS fingerprint impersonation под Chrome 120) и `requests`. Опрос баланса и сбор награды занимают **менее 0.5 секунды** без накладных расходов браузера.
  - **Автоматическая реавторизация:** встроенный Playwright Chromium используется только для первоначального входа и автоматического продления `hashed_token` при 401 Unauthorized.
- 🔄 **Строгая конечная машина состояний (Finite State Machine):**
  - Полный жизненный цикл: `UNINITIALIZED` ➔ `CHECKING` ➔ `READY` / `COOLDOWN` ➔ `CLAIMING` ➔ `VERIFYING` ➔ `CLAIMED` / `UNCERTAIN` / `FAILED`.
  - Защита от race conditions, повторных кликов и обрывов сети (idempotency & verification recovery).
- 👥 **Мульти-аккаунтность:**
  - Поддержка десятков аккаунтов с независимыми сессиями, раздельными балансами и тегами.
  - Загрузка из локального хранилища (`data/accounts.json`) или через единый JSON-секрет GitHub Actions (`THUNDR_ACCOUNTS_JSON`).
- 🔒 **Безопасность первого класса:**
  - Все токены (`hashed_token`) и пароли маскируются в логах и консоли (`qKih...KD8=`).
  - Файлы истории (`history.json`, `history.csv`) гарантированно не содержат конфиденциальных данных.
- 📱 **Telegram-уведомления:**
  - Форматированные сообщения (HTML) с эмодзи, прогрессом стрика, количеством заработанных монет и точным временем сброса кулдауна в часовом поясе **Europe/Berlin**.
  - Гибкие режимы: `all`, `rewards_only`, `errors_only`, `summary` (единая сводка по всем аккаунтам).
  - Автоматический fallback на чистый текст при ошибках парсинга Telegram API.
- 📊 **Аналитика и экспорт:**
  - Логирование каждой операции, динамика изменения баланса, экспорт в CSV и JSON.

---

## 📁 Структура проекта

```
thundr-daily-runner/
├── thundr/                   # Основной пакет логики
│   ├── __init__.py           # Экспорты и версия пакета
│   ├── account.py            # Модели аккаунтов, сессий и статусов
│   ├── client.py             # Гибридный клиент (curl_cffi API + Playwright)
│   ├── state_machine.py      # Конечная машина состояний с верификацией
│   ├── storage.py            # Хранение истории, аналитика и экспорт в CSV
│   └── telegram.py           # Модуль Telegram-уведомлений (Europe/Berlin)
├── tests/                    # Полный набор тестов (unit + mock + live)
│   ├── test_account.py
│   ├── test_cli.py
│   ├── test_client.py
│   ├── test_live_api.py      # Живые контрактные тесты реального API
│   ├── test_state_machine.py
│   ├── test_storage.py
│   └── test_telegram.py
├── data/                     # Директория данных (accounts.json, history.csv)
├── .github/workflows/
│   └── daily.yml             # GitHub Actions workflow (расписание 00:05 UTC)
├── cli.py                    # Полнофункциональный CLI (Click + Rich)
├── setup.ps1                 # Скрипт быстрой установки для PowerShell
├── setup.bat                 # Скрипт быстрой установки для CMD
├── requirements.txt          # Production зависимости
├── requirements-dev.txt      # Зависимости для тестирования
├── .env.example              # Шаблон конфигурации
└── README.md
```

---

## 🚀 Быстрый старт на Windows

### Вариант 1: Автоматический установщик

Запустите PowerShell от имени пользователя:
```powershell
.\setup.ps1
```
или в командной строке:
```cmd
setup.bat
```
Скрипт автоматически проверит Python, установит зависимости и скачает браузер Playwright.

### Вариант 2: Ручная установка

1. **Клонируйте репозиторий:**
   ```bash
   git clone https://github.com/GitGayHub/thundr-daily-runner.git
   cd thundr-daily-runner
   ```

2. **Установите зависимости:**
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

3. **Создайте `.env` файл:**
   ```bash
   cp .env.example .env
   ```

4. **Добавьте аккаунт Thundr:**
   ```bash
   python cli.py add-account
   ```
   Вам будет предложено ввести имя пользователя и пароль.

5. **Проверьте статус:**
   ```bash
   python cli.py check
   ```

6. **Запустите сбор наград:**
   ```bash
   python cli.py run
   ```

---

## 🛠️ Команды CLI

Интерфейс командной строки `cli.py` поддерживает богатый вывод с таблицами `rich`:

| Команда | Описание |
|---|---|
| `python cli.py run` | Полный цикл: опрос аккаунтов, сбор доступных кейсов, верификация баланса, отправка в Telegram |
| `python cli.py run --dry-run` | Симуляция без отправки запроса на сбор |
| `python cli.py run --no-telegram` | Запуск без отправки сообщений в Telegram |
| `python cli.py check` | Быстрая проверка баланса монет и статуса кейса без клейма |
| `python cli.py list-accounts` | Список всех аккаунтов с маскированными токенами |
| `python cli.py add-account` | Интерактивное или аргументное добавление аккаунта |
| `python cli.py remove-account <user>` | Удаление аккаунта из локального списка |
| `python cli.py export --format csv` | Экспорт истории сборов в CSV (`data/history.csv`) |
| `python cli.py test --with-browser` | Запуск диагностического тестирования сети, браузера и Telegram |

---

## ⚙️ Настройка GitHub Actions

Репозиторий настроен на автоматический запуск через GitHub Actions в `.github/workflows/daily.yml`.

### Расписание
- Кейс в Thundr сбрасывается ежедневно ровно в **00:00:00 UTC** (`next_reset_at`).
- Workflow запускается в **00:05 UTC** (через 5 минут после сброса, чтобы учесть дрейф серверного времени).
- Вспомогательный запуск в **12:05 UTC** для повторных попыток.
- Возможность запуска вручную через вкладку **Actions ➔ Run workflow**.

### Настройка Secrets в GitHub

Перейдите в репозитории: **Settings ➔ Secrets and variables ➔ Actions ➔ New repository secret**:

| Имя секрета | Описание | Пример |
|---|---|---|
| `THUNDR_ACCOUNTS_JSON` | JSON-массив ваших аккаунтов | `[{"username":"acc1","password":"pwd","user_id":"...","hashed_token":"..."}]` |
| `TELEGRAM_BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) | `123456789:ABCdefGhIJKlmNoPQRstuvWXyz` |
| `TELEGRAM_CHAT_ID` | Ваш числовой ID из [@userinfobot](https://t.me/userinfobot) | `987654321` |
| `TELEGRAM_MODE` | Режим уведомлений | `all` / `rewards_only` / `errors_only` / `summary` |

> 💡 **Совет:** Если указать `user_id` и `hashed_token` в `THUNDR_ACCOUNTS_JSON`, воркфлоу отработает мгновенно через быстрый API без необходимости запускать браузер! Если токен устареет, система автоматически запустит headless Playwright и обновит его по логину/паролю.

---

## 🧪 Тестирование

Проект покрыт всесторонними тестами с 100% прохождением:

```bash
# Запуск всех unit и интеграционных тестов
python -m pytest tests/ -v
```

Тестовый набор включает:
- `test_account.py` — проверка маскирования, сериализации и мульти-аккаунтного менеджера.
- `test_client.py` — проверка кодирования URL, ответа API (200, 401, 409, 429, 500) и авто-реавторизации.
- `test_state_machine.py` — проверка переходов состояний, восстановления при обрывах сети и идемпотентности.
- `test_storage.py` — проверка записи истории, подсчета суммарных метрик и CSV экспорта.
- `test_telegram.py` — проверка генерации сообщений, перевода часового пояса в Europe/Berlin и fallback режима.
- `test_cli.py` — симуляция всех консольных команд.
- `test_live_api.py` — живые проверки против боевого API id.thundr.com.

---

## 📄 Лицензия

MIT License. Создано исключительно для автоматизации личных разрешенных наград.
