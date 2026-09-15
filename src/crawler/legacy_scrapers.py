"""HTML scrapers from the original job_crawler.py (API boards use generic adapters)."""

from __future__ import annotations

import time

from bs4 import BeautifulSoup

from src.crawler.filters import is_relevant, is_worldwide
from src.crawler.http import clean, get


def scrape_djinni() -> list[dict]:
    jobs: list[dict] = []
    seen: set[str] = set()
    searches = [
        "https://djinni.co/jobs/?primary_keyword=Golang&employment=remote&region=worldwide",
        "https://djinni.co/jobs/?primary_keyword=Full+Stack&employment=remote&region=worldwide",
        "https://djinni.co/jobs/?primary_keyword=Golang&employment=remote",
        "https://djinni.co/jobs/?primary_keyword=Full+Stack&employment=remote",
    ]
    try:
        for url in searches:
            r = get(url)
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select("li.list-jobs__item"):
                title_el = card.select_one("a.profile") or card.select_one("a[class*='job']")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                job_url = f"https://djinni.co{href}" if href.startswith("/") else href
                if job_url in seen:
                    continue
                seen.add(job_url)
                company_el = card.select_one(".job-list-item__title, .company")
                salary_el = card.select_one(".public-salary-item, .salary")
                desc_el = card.select_one(".list-jobs__description, .job-list-item__description")
                loc_el = card.select_one(".location, .job-list-item__location")
                loc = loc_el.get_text(strip=True) if loc_el else "Remote"
                if not is_worldwide(loc):
                    continue
                jobs.append(
                    {
                        "source": "Djinni",
                        "title": title,
                        "company": company_el.get_text(strip=True) if company_el else "",
                        "location": loc,
                        "salary": salary_el.get_text(strip=True) if salary_el else "",
                        "url": job_url,
                        "description": clean(desc_el.get_text()) if desc_el else "",
                    }
                )
            time.sleep(2)
    except Exception as e:
        print(f"  [Djinni] {e}")
    print(f"  Djinni: {len(jobs)}")
    return jobs


def scrape_nodesk() -> list[dict]:
    jobs: list[dict] = []
    seen: set[str] = set()
    try:
        for path in ["/remote-jobs/golang", "/remote-jobs/full-stack"]:
            r = get(f"https://nodesk.co{path}/")
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select("article, .job-item, li.job"):
                title_el = card.select_one("h2 a, h3 a, .job-title a, a.title")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                job_url = href if href.startswith("http") else f"https://nodesk.co{href}"
                if job_url in seen:
                    continue
                seen.add(job_url)
                company_el = card.select_one(".company, .employer, .company-name")
                desc_el = card.select_one(".description, p")
                jobs.append(
                    {
                        "source": "NoDesk",
                        "title": title,
                        "company": company_el.get_text(strip=True) if company_el else "",
                        "location": "Worldwide Remote",
                        "url": job_url,
                        "description": clean(desc_el.get_text()) if desc_el else "",
                    }
                )
            time.sleep(1)
    except Exception as e:
        print(f"  [NoDesk] {e}")
    print(f"  NoDesk: {len(jobs)}")
    return jobs


def scrape_justremote() -> list[dict]:
    jobs: list[dict] = []
    seen: set[str] = set()
    try:
        for kw in ["golang", "full-stack"]:
            r = get(f"https://justremote.co/remote-developer-jobs?search={kw}")
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select(".job-card, article.job, .listing"):
                title_el = card.select_one("h2 a, h3 a, .job-title")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not is_relevant(title):
                    continue
                href = title_el.get("href", "")
                job_url = href if href.startswith("http") else f"https://justremote.co{href}"
                if job_url in seen:
                    continue
                seen.add(job_url)
                company_el = card.select_one(".company, .employer")
                salary_el = card.select_one(".salary")
                jobs.append(
                    {
                        "source": "JustRemote",
                        "title": title,
                        "company": company_el.get_text(strip=True) if company_el else "",
                        "location": "Worldwide Remote",
                        "salary": salary_el.get_text(strip=True) if salary_el else "",
                        "url": job_url,
                    }
                )
            time.sleep(1)
    except Exception as e:
        print(f"  [JustRemote] {e}")
    print(f"  JustRemote: {len(jobs)}")
    return jobs


def scrape_remoteco() -> list[dict]:
    jobs: list[dict] = []
    seen: set[str] = set()
    try:
        for path in ["/remote-jobs/developer/golang/", "/remote-jobs/developer/full-stack/"]:
            r = get(f"https://remote.co{path}")
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select(".job_listing, article, .listing-item"):
                title_el = card.select_one("h2 a, .position a, h3 a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not is_relevant(title):
                    continue
                href = title_el.get("href", "")
                job_url = href if href.startswith("http") else f"https://remote.co{href}"
                if job_url in seen:
                    continue
                seen.add(job_url)
                company_el = card.select_one(".company, .employer, .company_name")
                jobs.append(
                    {
                        "source": "Remote.co",
                        "title": title,
                        "company": company_el.get_text(strip=True) if company_el else "",
                        "location": "Worldwide Remote",
                        "url": job_url,
                    }
                )
            time.sleep(1)
    except Exception as e:
        print(f"  [Remote.co] {e}")
    print(f"  Remote.co: {len(jobs)}")
    return jobs


SCRAPERS = {
    "scrape_djinni": scrape_djinni,
    "scrape_nodesk": scrape_nodesk,
    "scrape_justremote": scrape_justremote,
    "scrape_remoteco": scrape_remoteco,
}
