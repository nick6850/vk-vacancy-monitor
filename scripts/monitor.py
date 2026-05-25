#!/usr/bin/env python3
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
STATE_PATH = DATA_DIR / "state.json"
REPORT_PATH = ROOT / "REPORT.md"
MONTHLY_STATS_PATH = DATA_DIR / "monthly_stats.json"
MSK = timezone(timedelta(hours=3))

LIST_URL = "https://team.vk.company/vacancy/?specialty=287"
VACANCY_URL = "https://team.vk.company/vacancy/{id}/"

STACK_KEYWORDS = [
    "JavaScript",
    "TypeScript",
    "React",
    "Redux",
    "Vue",
    "HTML",
    "CSS",
    "CSS-in-JS",
    "REST",
    "GraphQL",
    "Webpack",
    "Vite",
    "Rspack",
    "Jest",
    "React Testing Library",
    "Playwright",
    "Cypress",
    "Web Vitals",
    "Lighthouse",
    "Git",
    "CI/CD",
    "WebView",
]

CANONICAL_STACK = {
    keyword.lower(): keyword
    for keyword in STACK_KEYWORDS
}
CANONICAL_STACK.update(
    {
        "frontend": "Frontend",
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "react": "React",
        "redux": "Redux",
        "vue.js": "Vue",
        "vue": "Vue",
    }
)


class SiteContractError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(MSK).replace(microsecond=0).isoformat()


def now_msk() -> datetime:
    return datetime.now(MSK).replace(microsecond=0)


def today() -> str:
    return datetime.now(MSK).strftime("%Y-%m-%d")


def fetch(url: str, retries: int = 3) -> str:
    headers = {
        "User-Agent": "vk-vacancy-monitor/1.0 (+personal career tracking)",
        "Accept-Language": "ru,en;q=0.8",
    }
    request = urllib.request.Request(url, headers=headers)
    last_error = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def extract_next_data(page_html: str) -> dict:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        page_html,
        flags=re.S,
    )
    if not match:
        vacancy_links = sorted(set(re.findall(r"/vacancy/(\d+)/", page_html)))
        raise SiteContractError(
            "Could not find __NEXT_DATA__ in vacancy list page. "
            f"HTML still contains {len(vacancy_links)} vacancy links."
        )
    try:
        return json.loads(html.unescape(match.group(1)))
    except json.JSONDecodeError as exc:
        raise SiteContractError(f"Could not parse __NEXT_DATA__ JSON: {exc}") from exc


def normalize_list_vacancy(item: dict) -> dict:
    if not isinstance(item, dict):
        raise SiteContractError(f"Vacancy item must be an object, got {type(item).__name__}")
    if not item.get("id") or not item.get("title"):
        raise SiteContractError(f"Vacancy item missing id/title: {item!r}")

    vacancy_id = str(item["id"])
    tags = [tag["name"] for tag in item.get("tags") or [] if tag.get("name")]
    return {
        "id": vacancy_id,
        "title": item.get("title") or "",
        "project": (item.get("group") or {}).get("name") or "",
        "city": (item.get("town") or {}).get("name") or "",
        "work_format": item.get("work_format") or "",
        "tags": sorted(set(tags), key=str.lower),
        "url": VACANCY_URL.format(id=vacancy_id),
    }


def text_from_html(page_html: str) -> str:
    page_html = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", page_html)
    page_html = re.sub(r"(?s)<[^>]+>", " ", page_html)
    page_html = html.unescape(page_html)
    return re.sub(r"\s+", " ", page_html).strip()


def find_stack(text: str, tags: list[str]) -> list[str]:
    found = {CANONICAL_STACK.get(tag.lower(), tag) for tag in tags}
    lowered = text.lower()
    for keyword in STACK_KEYWORDS:
        if keyword.lower() in lowered:
            found.add(CANONICAL_STACK.get(keyword.lower(), keyword))
    return sorted(found, key=str.lower)


def normalize_stack(stack: list[str]) -> list[str]:
    return sorted({CANONICAL_STACK.get(item.lower(), item) for item in stack}, key=str.lower)


def find_level(text: str) -> str:
    for level in ["intern", "junior", "middle", "senior", "lead"]:
        if re.search(rf"\b{level}\b", text, flags=re.I):
            return level
    return ""


def enrich_vacancy(vacancy: dict) -> dict:
    page_html = fetch(vacancy["url"])
    text = text_from_html(page_html)
    enriched = dict(vacancy)
    enriched["stack"] = find_stack(text, vacancy.get("tags", []))
    enriched["level"] = find_level(text)
    enriched["description_sample"] = text[:500]
    return enriched


def load_current_vacancies() -> tuple[list[dict], int]:
    page_html = fetch(LIST_URL)
    data = extract_next_data(page_html)
    try:
        page_props = data["props"]["pageProps"]
        raw_vacancies = page_props["initialVacancies"]
    except KeyError as exc:
        raise SiteContractError(f"Expected Next.js pageProps key is missing: {exc}") from exc

    if not isinstance(raw_vacancies, list):
        raise SiteContractError("Expected pageProps.initialVacancies to be a list")

    vacancies = [normalize_list_vacancy(item) for item in raw_vacancies]
    total = page_props.get("initialTotalCount", len(vacancies))
    if not isinstance(total, int):
        raise SiteContractError("Expected pageProps.initialTotalCount to be an integer")
    if total != len(vacancies):
        raise SiteContractError(
            f"Expected all vacancies to be present in initialVacancies, got {len(vacancies)} of {total}"
        )
    return vacancies, total


def update_state(current: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    timestamp = now_iso()
    state = load_json(STATE_PATH, {"vacancies": {}, "runs": []})
    known = state.setdefault("vacancies", {})
    current_by_id = {vacancy["id"]: vacancy for vacancy in current}

    new_vacancies = []
    closed_vacancies = []

    for vacancy_id, vacancy in current_by_id.items():
        if vacancy_id not in known:
            enriched = enrich_vacancy(vacancy)
            known[vacancy_id] = {
                **enriched,
                "first_seen": timestamp,
                "last_seen": timestamp,
                "status": "active",
                "closed_at": "",
            }
            new_vacancies.append(known[vacancy_id])
        else:
            previous = known[vacancy_id]
            known[vacancy_id] = {
                **previous,
                **vacancy,
                "stack": normalize_stack(previous.get("stack", vacancy.get("tags", []))),
                "last_seen": timestamp,
                "status": "active",
                "closed_at": "",
            }

    for vacancy_id, vacancy in known.items():
        if vacancy.get("status") == "active" and vacancy_id not in current_by_id:
            vacancy["status"] = "closed"
            vacancy["closed_at"] = timestamp
            closed_vacancies.append(vacancy)

    active_count = sum(1 for vacancy in known.values() if vacancy.get("status") == "active")
    state.setdefault("runs", []).append(
        {
            "checked_at": timestamp,
            "active_count": active_count,
            "new_count": len(new_vacancies),
            "closed_count": len(closed_vacancies),
        }
    )
    state["runs"] = state["runs"][-400:]
    save_json(STATE_PATH, state)
    return state, new_vacancies, closed_vacancies


def days_between(start: str, end: str) -> int:
    if not start or not end:
        return 0
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    return max(0, (end_dt - start_dt).days)


def parse_iso(timestamp: str) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp)
    except ValueError:
        return None


def month_key(timestamp: str) -> str:
    if not timestamp:
        return "unknown"
    return timestamp[:7]


def build_monthly_stats(state: dict) -> dict:
    monthly = {}
    for vacancy in state.get("vacancies", {}).values():
        first_seen_month = month_key(vacancy.get("first_seen", ""))
        monthly.setdefault(first_seen_month, {"new": 0, "closed": 0, "active_end": 0})
        monthly[first_seen_month]["new"] += 1

        closed_at = vacancy.get("closed_at")
        if closed_at:
            closed_month = month_key(closed_at)
            monthly.setdefault(closed_month, {"new": 0, "closed": 0, "active_end": 0})
            monthly[closed_month]["closed"] += 1

    runs_by_month = {}
    for run in state.get("runs", []):
        runs_by_month[month_key(run.get("checked_at", ""))] = run

    for month, run in runs_by_month.items():
        monthly.setdefault(month, {"new": 0, "closed": 0, "active_end": 0})
        monthly[month]["active_end"] = run.get("active_count", 0)

    return dict(sorted(monthly.items(), reverse=True))


def write_monthly_stats(state: dict) -> dict:
    monthly = build_monthly_stats(state)
    save_json(MONTHLY_STATS_PATH, monthly)
    return monthly


def count_recent(vacancies: list[dict], field: str, since: datetime) -> int:
    total = 0
    for vacancy in vacancies:
        timestamp = parse_iso(vacancy.get(field, ""))
        if timestamp and timestamp >= since:
            total += 1
    return total


def top_counts(values: list[str], limit: int = 4) -> str:
    counts = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return "-"
    items = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[:limit]
    return ", ".join(f"{name} {count}" for name, count in items)


def active_stack_summary(active: list[dict], limit: int = 6) -> str:
    values = []
    for vacancy in active:
        values.extend(vacancy.get("stack", []))
    return top_counts(values, limit=limit)


def short_vacancy(vacancy: dict) -> str:
    title = vacancy.get("title", "").strip()
    project = vacancy.get("project", "").strip()
    city = vacancy.get("city", "").strip()
    parts = [part for part in [title, project, city] if part]
    return " / ".join(parts)


def short_vacancy_link(vacancy: dict) -> str:
    title = vacancy.get("title", "").strip()
    project = vacancy.get("project", "").strip()
    url = vacancy.get("url", "").strip()
    label = f"{title} — {project}" if project else title
    return f"{label}: {url}" if url else label


def list_summary(title: str, vacancies: list[dict], limit: int = 3) -> list[str]:
    if not vacancies:
        return [f"{title}: нет"]
    lines = [f"{title}:"]
    for vacancy in vacancies[:limit]:
        lines.append(f"- {short_vacancy_link(vacancy)}")
    if len(vacancies) > limit:
        lines.append(f"- ...и еще {len(vacancies) - limit}")
    return lines


def telegram_digest_message(state: dict, new_vacancies: list[dict], closed_vacancies: list[dict]) -> str:
    vacancies = list(state.get("vacancies", {}).values())
    active = [vacancy for vacancy in vacancies if vacancy.get("status") == "active"]
    now = now_msk()
    week_start = now - timedelta(days=7)
    month_start = now.replace(day=1, hour=0, minute=0, second=0)

    week_new = count_recent(vacancies, "first_seen", week_start)
    week_closed = count_recent(vacancies, "closed_at", week_start)
    month_new = count_recent(vacancies, "first_seen", month_start)
    month_closed = count_recent(vacancies, "closed_at", month_start)

    month_name = now.strftime("%m.%Y")

    lines = [
        f"Твой дайджест VK Frontend на эту неделю — {now.strftime('%d.%m.%Y')}",
        "",
        f"Открытых вакансий сейчас: {len(active)}",
        f"За неделю: новых {week_new}, закрытых {week_closed}",
        f"За месяц ({month_name}): новых {month_new}, закрытых {month_closed}",
        f"Где чаще ищут: {top_counts([vacancy.get('project', '') for vacancy in active])}",
        f"Формат работы: {top_counts([vacancy.get('work_format', '') for vacancy in active], limit=3)}",
        f"Стек в активных вакансиях: {active_stack_summary(active)}",
        "",
        *list_summary("Новые вакансии с прошлого дайджеста", new_vacancies),
        *list_summary("Закрылись с прошлого дайджеста", closed_vacancies),
        "",
        "Полная история и график: https://github.com/nick6850/vk-vacancy-monitor/blob/main/REPORT.md",
    ]

    message = "\n".join(lines)
    if len(message) <= 1000:
        return message
    compact_lines = [
        lines[0],
        "",
        *lines[2:8],
        "",
        f"Новые с прошлого дайджеста: {len(new_vacancies)}",
        f"Закрылись с прошлого дайджеста: {len(closed_vacancies)}",
        "Полная история: https://github.com/nick6850/vk-vacancy-monitor/blob/main/REPORT.md",
    ]
    return "\n".join(compact_lines)[:1000]


def write_snapshot(current: list[dict], total: int) -> None:
    save_json(
        SNAPSHOT_DIR / f"{today()}.json",
        {
            "checked_at": now_iso(),
            "source": LIST_URL,
            "total": total,
            "vacancies": current,
        },
    )


def write_report(state: dict) -> None:
    vacancies = list(state.get("vacancies", {}).values())
    active = [vacancy for vacancy in vacancies if vacancy.get("status") == "active"]
    closed = [vacancy for vacancy in vacancies if vacancy.get("status") == "closed"]
    stack_counts = {}
    for vacancy in vacancies:
        for keyword in vacancy.get("stack", []):
            stack_counts[keyword] = stack_counts.get(keyword, 0) + 1

    top_stack = sorted(stack_counts.items(), key=lambda item: (-item[1], item[0].lower()))[:15]
    newest = sorted(active, key=lambda vacancy: vacancy.get("first_seen", ""), reverse=True)[:10]
    runs = state.get("runs", [])
    last_run = runs[-1] if runs else {}
    monthly = write_monthly_stats(state)

    lines = [
        "# VK Frontend Vacancy Monitor",
        "",
        f"Last check: `{last_run.get('checked_at', '')}`",
        f"Active vacancies: **{len(active)}**",
        f"Closed since monitoring started: **{len(closed)}**",
        "",
        "## Monthly dynamics",
        "",
        "| Month | New | Closed | Active at last check |",
        "| --- | ---: | ---: | ---: |",
    ]

    for month, stats in list(monthly.items())[:18]:
        lines.append(
            f"| {month} | {stats.get('new', 0)} | {stats.get('closed', 0)} | "
            f"{stats.get('active_end', 0)} |"
        )

    lines.extend(
        [
            "",
            "## Newest active vacancies",
            "",
        ]
    )

    if newest:
        for vacancy in newest:
            stack = ", ".join(vacancy.get("stack", [])[:8]) or "-"
            lines.append(
                f"- [{vacancy['title']}]({vacancy['url']}) — {vacancy.get('project', '')}, "
                f"{vacancy.get('city', '')}, {vacancy.get('work_format', '')}; stack: {stack}"
            )
    else:
        lines.append("- No active vacancies found.")

    lines.extend(["", "## Stack frequency", ""])
    if top_stack:
        for keyword, count in top_stack:
            lines.append(f"- {keyword}: {count}")
    else:
        lines.append("- No stack data yet.")

    lines.extend(["", "## Recently closed", ""])
    recent_closed = sorted(closed, key=lambda vacancy: vacancy.get("closed_at", ""), reverse=True)[:10]
    if recent_closed:
        for vacancy in recent_closed:
            days_open = days_between(vacancy.get("first_seen", ""), vacancy.get("closed_at", ""))
            lines.append(f"- {vacancy['title']} — {vacancy.get('project', '')}; open for {days_open} days")
    else:
        lines.append("- No closed vacancies yet.")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def telegram_message(new_vacancies: list[dict], closed_vacancies: list[dict]) -> str:
    parts = []
    if new_vacancies:
        parts.append("Новые frontend-вакансии VK:")
        for vacancy in new_vacancies:
            stack = ", ".join(vacancy.get("stack", [])[:8]) or "-"
            parts.append(
                f"\n{vacancy['title']}\n"
                f"{vacancy.get('project', '')}, {vacancy.get('city', '')}, {vacancy.get('work_format', '')}\n"
                f"Стек: {stack}\n"
                f"{vacancy['url']}"
            )
    if closed_vacancies:
        parts.append("\nЗакрылись вакансии:")
        for vacancy in closed_vacancies:
            parts.append(f"\n{vacancy['title']} — {vacancy.get('project', '')}\n{vacancy['url']}")
    return "\n".join(parts).strip()


def should_send_test_notification() -> bool:
    return os.environ.get("SEND_TEST_NOTIFICATION", "").lower() in {"1", "true", "yes", "on"}


def send_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not message or not token or not chat_id:
        return

    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": "true",
        }
    ).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def send_telegram_photo(photo_path: str, caption: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id or not photo_path:
        return False

    path = Path(photo_path)
    if not path.exists():
        return False

    boundary = f"----vk-vacancy-monitor-{uuid.uuid4().hex}"
    body = bytearray()

    def add_field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    add_field("chat_id", chat_id)
    add_field("caption", caption[:1024])

    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        (
            f'Content-Disposition: form-data; name="photo"; filename="{path.name}"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
    )
    body.extend(path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        response.read()
    return True


def github_run_url() -> str:
    server_url = os.environ.get("GITHUB_SERVER_URL")
    repository = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server_url and repository and run_id:
        return f"{server_url}/{repository}/actions/runs/{run_id}"
    return ""


def send_failure_alert(exc: Exception) -> None:
    if isinstance(exc, SiteContractError):
        title = "VK vacancy monitor: изменилась структура сайта"
    else:
        title = "VK vacancy monitor: ошибка запуска"

    message = (
        f"{title}\n\n"
        f"Время: {now_iso()}\n"
        f"Страница: {LIST_URL}\n"
        f"Ошибка: {type(exc).__name__}: {exc}"
    )
    run_url = github_run_url()
    if run_url:
        message += f"\nGitHub Actions: {run_url}"
    try:
        send_telegram(message[:3900])
    except Exception as alert_exc:
        print(f"Could not send Telegram failure alert: {alert_exc}", file=sys.stderr)


def main() -> int:
    DATA_DIR.mkdir(exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        current, total = load_current_vacancies()
        write_snapshot(current, total)
        state, new_vacancies, closed_vacancies = update_state(current)
        write_report(state)

        message = telegram_digest_message(state, new_vacancies, closed_vacancies)
        if should_send_test_notification():
            message = "Тестовый запуск\n\n" + message

        screenshot_path = os.environ.get("SCREENSHOT_PATH", "")
        photo_sent = False
        try:
            photo_sent = send_telegram_photo(screenshot_path, message)
        except Exception as exc:
            print(f"Could not send Telegram screenshot: {exc}", file=sys.stderr)

        if not photo_sent:
            fallback_message = message
            if screenshot_path:
                fallback_message += "\n\nСкриншот не приложился: шаг скриншота не создал файл или Telegram не принял картинку."
            send_telegram(fallback_message)

        print(
            json.dumps(
                {
                    "active": len(current),
                    "new": len(new_vacancies),
                    "closed": len(closed_vacancies),
                    "notified": bool(message and os.environ.get("TELEGRAM_BOT_TOKEN")),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        send_failure_alert(exc)
        raise


if __name__ == "__main__":
    sys.exit(main())
