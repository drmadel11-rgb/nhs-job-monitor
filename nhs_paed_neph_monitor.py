import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("seen_nhs_jobs.json")

SEARCH_QUERIES = [
    "paediatric nephrology fellow",
    "pediatric nephrology fellow",
    "paediatric nephrology registrar",
    "pediatric nephrology trust fellow",
]

TRAC_QUERIES = [
    "paediatric nephrology",
    "pediatric nephrology",
]

# Verified career/portal URLs for UK paediatric nephrology trusts/centres
# (Most trusts advertise on central apps.trac.jobs / nhsjobs.com / jobs.nhs.uk)
CENTRE_PORTAL_URLS: Dict[str, str] = {
    "Great Ormond Street Hospital (GOSH)": "https://www.gosh.nhs.uk/working-here/working-us/current-jobs/",
    "Leeds Teaching Hospitals (Leeds Children's Hospital)": "https://vacancies.leedsteachinghospitals.jobs/",
    "Manchester University NHS Foundation Trust (RMCH)": "https://mft.nhs.uk/careers/",
    "University Hospital Southampton (Southampton Children's)": "https://careers.uhs.nhs.uk/jobs",
}

NEPHROLOGY_PATTERN = re.compile(r"\b(paediatric|pediatric)\s+nephrology\b", re.I)
ROLE_PATTERN = re.compile(
    r"\b(fellow|junior\s+fellow|senior\s+fellow|trust\s+fellow|registrar|spr|st[3-8]|clinical\s+fellow)\b",
    re.I,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; NHSJobWatcher/1.2; +mailto:your-email@example.com)"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}


def load_seen_jobs(path: Path = STATE_FILE) -> Dict[str, dict]:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_seen_jobs(seen: Dict[str, dict], path: Path = STATE_FILE) -> None:
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)


def is_matching_role(title: str) -> bool:
    return bool(NEPHROLOGY_PATTERN.search(title) and ROLE_PATTERN.search(title))


def fetch_html(url: str, timeout: int = 20) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        print(f"[WARN] Failed to fetch {url}: {exc}")
        return None


def parse_nhs_jobs(query: str) -> List[dict]:
    """Search NHS Jobs (jobs.nhs.uk) and extract matching job postings."""
    url = f"https://www.jobs.nhs.uk/candidate/search/results?keyword={quote_plus(query)}"
    html = fetch_html(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results: List[dict] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "/candidate/jobadvert/" not in href:
            continue

        title = " ".join(anchor.get_text(" ", strip=True).split())
        if len(title) < 6 or not is_matching_role(title):
            continue

        full_url = urljoin("https://www.jobs.nhs.uk", href.split("?")[0])
        job_id_match = re.search(r"/jobadvert/([^/?#]+)", full_url)
        job_id = f"nhs:{job_id_match.group(1)}" if job_id_match else f"nhs:{full_url}"

        container = anchor.find_parent(["li", "article", "div"])
        meta_text = (
            " ".join(container.get_text(" ", strip=True).split()) if container else title
        )

        results.append(
            {
                "id": job_id,
                "source": "NHS Jobs",
                "title": title,
                "url": full_url,
                "snippet": meta_text[:280],
            }
        )

    return results


def parse_trac_and_centres() -> List[dict]:
    """Search central Trac feeds and verified UK paediatric nephrology trust pages."""
    sources: List[tuple[str, str]] = [
        ("Trac Nephrology Feed", "https://www.nhsjobs.com/job_list/Medical_and_Dental/s2/Medical_Nephrology/d562"),
    ] + [
        ("Trac Search", f"https://apps.trac.jobs/job-list?search={quote_plus(q)}")
        for q in TRAC_QUERIES
    ] + [
        (centre_name, portal_url)
        for centre_name, portal_url in CENTRE_PORTAL_URLS.items()
    ]

    results: List[dict] = []

    for source_label, url in sources:
        html = fetch_html(url)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            title = " ".join(anchor.get_text(" ", strip=True).split())
            if len(title) < 6 or not is_matching_role(title):
                continue

            full_url = urljoin(url, anchor["href"].split("?")[0])
            id_match = re.search(r"/job[-_]advert/(\d+)", full_url)
            job_id = (
                f"trac:{id_match.group(1)}"
                if id_match
                else f"portal:{full_url}"
            )

            container = anchor.find_parent(["li", "article", "div", "tr"])
            meta_text = (
                " ".join(container.get_text(" ", strip=True).split())
                if container
                else title
            )

            results.append(
                {
                    "id": job_id,
                    "source": source_label,
                    "title": title,
                    "url": full_url,
                    "snippet": meta_text[:280],
                }
            )

        time.sleep(1.2)

    return results


def parse_bmj_careers(query: str) -> List[dict]:
    """Search BMJ Careers (careers.bmj.com) and extract matching job postings."""
    url = f"https://careers.bmj.com/jobs/?q={quote_plus(query)}"
    html = fetch_html(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results: List[dict] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "/job/" not in href:
            continue

        title = " ".join(anchor.get_text(" ", strip=True).split())
        if len(title) < 6 or not is_matching_role(title):
            continue

        full_url = urljoin("https://careers.bmj.com", href.split("?")[0])
        job_id = f"bmj:{full_url}"

        container = anchor.find_parent(["li", "article", "div"])
        meta_text = (
            " ".join(container.get_text(" ", strip=True).split()) if container else title
        )

        results.append(
            {
                "id": job_id,
                "source": "BMJ Careers",
                "title": title,
                "url": full_url,
                "snippet": meta_text[:280],
            }
        )

    return results


def collect_jobs() -> Dict[str, dict]:
    found: Dict[str, dict] = {}

    for item in parse_trac_and_centres():
        found[item["id"]] = item

    for q in SEARCH_QUERIES:
        for item in parse_nhs_jobs(q) + parse_bmj_careers(q):
            found[item["id"]] = item
        time.sleep(1.5)

    return found


def format_alert_message(new_jobs: List[dict]) -> str:
    lines = [f"New NHS Paediatric Nephrology roles found ({len(new_jobs)}):\n"]
    for job in new_jobs:
        lines.append(f"• [{job['source']}] {job['title']}")
        lines.append(f"  {job['url']}\n")
    return "\n".join(lines).strip()


def send_email_alert(subject: str, body: str) -> None:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    email_from = os.getenv("ALERT_EMAIL_FROM", smtp_user or "")
    email_to = os.getenv("ALERT_EMAIL_TO")

    if not (smtp_host and email_from and email_to):
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        server.starttls()
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.send_message(msg)
    print(f"[INFO] Email alert sent to {email_to}")


def send_telegram_alert(body: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        return

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(
            api_url,
            json={
                "chat_id": chat_id,
                "text": body[:4000],
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        resp.raise_for_status()
        print("[INFO] Telegram alert sent")
    except requests.RequestException as exc:
        print(f"[WARN] Telegram alert failed: {exc}")


def run_once(seen: Dict[str, dict], seed_only: bool = False) -> None:
    current = collect_jobs()
    now_iso = datetime.now(timezone.utc).isoformat()

    if seed_only:
        for job_id, job in current.items():
            seen[job_id] = {**job, "first_seen": now_iso}
        save_seen_jobs(seen)
        print(f"[INFO] Seeded {len(seen)} existing postings without alerting.")
        return

    new_jobs = [job for job_id, job in current.items() if job_id not in seen]
    for job_id, job in current.items():
        if job_id not in seen:
            seen[job_id] = {**job, "first_seen": now_iso}

    save_seen_jobs(seen)

    if new_jobs:
        message = format_alert_message(new_jobs)
        print(message)
        send_email_alert(
            subject=f"New NHS Paediatric Nephrology Role ({len(new_jobs)})",
            body=message,
        )
        send_telegram_alert(message)
    else:
        print(f"[{now_iso}] No new matching roles (tracked total: {len(seen)}).")


def main() -> None:
    seen = load_seen_jobs()
    is_first_run = len(seen) == 0 or "--seed" in sys.argv
    run_once(seen, seed_only=is_first_run)

    if "--once" in sys.argv:
        return

    while True:
        time.sleep(3600)
        run_once(seen, seed_only=False)


if __name__ == "__main__":
    main()
