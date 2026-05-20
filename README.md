# VK Frontend Vacancy Monitor

Бесплатный мониторинг frontend-вакансий VK:

- раз в день проверяет `https://team.vk.company/vacancy/?specialty=287`;
- сохраняет ежедневный снимок в `data/snapshots`;
- ведет историю в `data/state.json`;
- считает динамику по месяцам в `data/monthly_stats.json`;
- обновляет краткий отчет `REPORT.md`;
- присылает Telegram-уведомление, если появились или закрылись вакансии;
- прикладывает к уведомлению полный скриншот страницы вакансий.
- присылает Telegram-алерт и останавливается, если изменилась структура сайта.

## Как это работает бесплатно

GitHub Actions запускает `scripts/monitor.py` по расписанию. Скрипт использует только стандартную библиотеку Python, поэтому ничего платного и внешних Python-пакетов не нужно.

Скриншот делает Playwright в GitHub Actions. Это тоже бесплатно в рамках GitHub Actions, отдельный хостинг или сервер не нужен.

Парсер намеренно строгий: он ожидает текущий контракт данных сайта (`__NEXT_DATA__`, `pageProps.initialVacancies`, `id/title`). Если контракт ломается, история не обновляется и скрипт не делает вид, что все нормально. Вместо этого он отправляет ошибку в Telegram, а GitHub Actions помечает запуск красным.

## Одноразовая настройка

1. Создай приватный или публичный GitHub-репозиторий.
2. Загрузи туда содержимое этой папки.
3. В Telegram открой `@BotFather`, создай бота и получи токен.
4. Напиши своему боту любое сообщение.
5. Узнай свой `chat_id`:

   ```bash
   curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates"
   ```

6. В GitHub открой `Settings -> Secrets and variables -> Actions -> New repository secret`.
7. Добавь два секрета:

   ```text
   TELEGRAM_BOT_TOKEN
   TELEGRAM_CHAT_ID
   ```

8. Открой `Actions -> Monitor VK frontend vacancies -> Run workflow`.

После этого проверка будет запускаться каждый день в `07:15 UTC`, то есть примерно в `10:15` по Москве.

## Локальный запуск

```bash
python3 scripts/monitor.py
```

Если переменные `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` не заданы, скрипт просто обновит данные и отчет без уведомления.

Локально скриншот можно сделать так:

```bash
npm install
npx playwright install chromium
SCREENSHOT_PATH=data/latest-page.png npm run screenshot
SCREENSHOT_PATH=data/latest-page.png python3 scripts/monitor.py
```

## Что смотреть

- `REPORT.md` — короткая сводка.
- `data/state.json` — вся накопленная история.
- `data/monthly_stats.json` — сколько вакансий появилось и закрылось по месяцам.
- `data/snapshots/*.json` — ежедневные снимки списка вакансий.

## Что считается

- новые вакансии;
- новые вакансии по месяцам;
- закрытые вакансии;
- закрытые вакансии по месяцам;
- активные вакансии;
- проект, город, формат работы;
- теги и найденные технологии из текста вакансии;
- примерная длительность жизни закрытых вакансий.
