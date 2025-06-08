
# Auto‑Cleaner for AstanaHunters bot

**Назначение:** искать OLD‑блоки в `bot.py`, получать подтверждение в Telegram, переносить их в `changelog.md`, делать резервную копию.

## Шаблон комментирования

```
# === OLD BLOCK START === v1.3 2025-06-06 (описание)
... старый код ...
# === OLD BLOCK END ===
```

## Быстрый запуск

```bash
CLEANER_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN \
CLEANER_ADMIN_ID=YOUR_TELEGRAM_ID \
python auto_cleaner.py
```

### Параметры

* `CLEANER_BOT_TOKEN` — токен бота‑уведомителя  
* `CLEANER_ADMIN_ID` — ваш Telegram user_id  
* `CONFIRM_TIMEOUT` — время ожидания ответа (по‑умолчанию 1 час)

## Планировщик

### Windows (Task Scheduler)

1. Открыть «Планировщик заданий» → «Создать задачу».
2. Триггер — «Раз в месяц», 1 числа.
3. Действие — `python E:\mytelegrambot\auto_cleaner.py`.
4. В «Дополнительно» поставить «Запуск от имени администратора».

### Linux (cron)

```
0 3 1 * * /usr/bin/python3 /home/user/mytelegrambot/auto_cleaner.py
```

## Что делает скрипт

1. **Резерв** `bot_backup_YYYY-MM-DD.py` в папке `backups`.
2. Отправляет отчёт вам в Telegram.
3. Ждёт `/ok` или `/cancel`.
4. При `ok` — переносит блоки в `changelog.md`, оставляет stub в `bot.py`.
5. Через месяц очередной запуск полностью удаляет stub.
