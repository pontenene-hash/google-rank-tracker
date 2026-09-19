from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "rank_config.json"
HISTORY_PATH = APP_DIR / "rank_history.csv"
SERPER_ENDPOINT = "https://google.serper.dev/search"
HISTORY_COLUMNS = [
    "checked_at",
    "target_url",
    "keyword",
    "rank",
    "matched_url",
    "provider",
]


def normalize_url(value: str) -> str:
    value = value.strip()
    if value and not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    return f"{host}{path}"


def is_domain_match(result_url: str, target_url: str) -> bool:
    result_domain = normalize_url(result_url).split("/", 1)[0]
    target_domain = normalize_url(target_url).split("/", 1)[0]
    return bool(result_domain) and result_domain == target_domain


def fetch_rank(keyword: str, target_url: str, api_key: str) -> tuple[int | None, str | None]:
    response = requests.post(
        SERPER_ENDPOINT,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": keyword, "gl": "jp", "hl": "ja", "num": 100},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("message"):
        raise RuntimeError(str(data["message"]))
    for fallback_position, item in enumerate(data.get("organic", []), start=1):
        link = item.get("link", "")
        if link and is_domain_match(link, target_url):
            return int(item.get("position", fallback_position)), link
    return None, None


def load_existing_history() -> pd.DataFrame:
    if not HISTORY_PATH.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    history = pd.read_csv(HISTORY_PATH)
    for column in HISTORY_COLUMNS:
        if column not in history.columns:
            history[column] = None
    return history[HISTORY_COLUMNS]


def main() -> int:
    api_key = os.getenv("SERPER_API_KEY", "").strip()
    if not api_key:
        print("SERPER_API_KEY が設定されていません。", file=sys.stderr)
        return 1

    with CONFIG_PATH.open(encoding="utf-8") as file:
        config = json.load(file)

    checked_at = datetime.now(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None).isoformat(timespec="seconds")
    new_rows: list[dict] = []
    failed_count = 0
    for site in config.get("sites", []):
        target_url = site["url"].strip()
        for keyword in dict.fromkeys(site.get("keywords", [])):
            keyword = keyword.strip()
            if not keyword:
                continue
            try:
                rank, matched_url = fetch_rank(keyword, target_url, api_key)
                label = f"{rank}位" if rank is not None else "100位圏外"
                print(f"{site.get('label', target_url)} | {keyword} | {label}")
                new_rows.append(
                    {
                        "checked_at": checked_at,
                        "target_url": target_url,
                        "keyword": keyword,
                        "rank": rank,
                        "matched_url": matched_url,
                        "provider": "serper-auto",
                    }
                )
            except Exception as exc:
                failed_count += 1
                print(f"ERROR | {target_url} | {keyword} | {exc}", file=sys.stderr)

    if not new_rows:
        print("保存できる計測結果がありません。", file=sys.stderr)
        return 1

    history = pd.concat([load_existing_history(), pd.DataFrame(new_rows)], ignore_index=True)
    parsed_dates = pd.to_datetime(history["checked_at"], errors="coerce", format="mixed")
    cutoff = pd.Timestamp(datetime.now(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None) - timedelta(days=366))
    history = history[parsed_dates >= cutoff]
    history = history.drop_duplicates().sort_values("checked_at")
    history.to_csv(HISTORY_PATH, index=False, encoding="utf-8-sig")
    print(f"{len(new_rows)}件を {HISTORY_PATH.name} に保存しました。")
    if failed_count:
        print(f"警告: {failed_count}件は計測に失敗しました。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
