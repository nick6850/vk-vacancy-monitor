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
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
STATE_PATH = DATA_DIR / "state.json"
REPORT_PATH = ROOT / "REPORT.md"
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


def now_iso() -> str:
    return datetime.now(MSK).replace(microsecond=0).isoformat()


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
        raise RuntimeError("Could not find __NEXT_DATA__ in vacancy list page")
    return json.loads(html.unescape(match.group(1)))


def normalize_list_vacancy(item: dict) -> dict:
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
    page_props = data["props"]["pageProps"]
    vacancies = [normalize_list_vacancy(item) for item in page_props["initialVacancies"]]
    return vacancies, int(page_props.get("initialTotalCount") or len(vacancies))


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

    lines = [
        "# VK Frontend Vacancy Monitor",
        "",
        f"Last check: `{last_run.get('checked_at', '')}`",
        f"Active vacancies: **{len(active)}**",
        f"Closed since monitoring started: **{len(closed)}**",
        "",
        "## Newest active vacancies",
        "",
    ]

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


def main() -> int:
    DATA_DIR.mkdir(exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    current, total = load_current_vacancies()
    write_snapshot(current, total)
    state, new_vacancies, closed_vacancies = update_state(current)
    write_report(state)

    message = telegram_message(new_vacancies, closed_vacancies)
    send_telegram(message)

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


if __name__ == "__main__":
    sys.exit(main())
