#!/usr/bin/env python3
"""Download and import legacy Twitter HTML captures from Wayback Machine.

The main archiver intentionally queries only ``application/json`` captures.
This tool preserves ``text/html`` captures separately and converts parseable
tweet pages into the v2 JSON shape consumed by ``archive.py render-html`` and
``archive.py build-index``.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_module
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


CDX_URL = "https://web.archive.org/cdx/search/cdx"
# ``if_`` is materially more reliable for Twitter HTML captures than the raw
# ``id_`` replay endpoint. It preserves the captured page body and only adds
# Wayback replay metadata, which the parsers ignore.
WAYBACK_URL = "https://web.archive.org/web/{timestamp}if_/{original}"
TWITTER_EPOCH_MS = 1288834974657
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; LegacyTwitterArchiver/1.0)"
STATUS_ID_RE = re.compile(r"/status/(\d+)")
PROFILE_ID_RE = re.compile(r"/profile_images/(\d+)/")
MEDIA_URL_RE = re.compile(r"https?://(?:pbs\.)?twimg\.com/media/", re.I)


@dataclass(frozen=True)
class Capture:
    timestamp: str
    original: str
    mimetype: str
    statuscode: str
    digest: str
    length: str
    tweet_id: str

    @property
    def key(self) -> str:
        return f"{self.timestamp}|{self.original}"


@dataclass
class ParsedTweet:
    tweet_id: str
    text: str
    created_at: str
    author_id: str
    author_name: str
    author_username: str
    author_avatar: str
    conversation_id: str
    reply_to_id: str
    images: list[str]
    capture_timestamp: str
    original_url: str
    parser: str

    @property
    def score(self) -> int:
        parser_score = {"old-dom": 300, "modern-dom": 300, "opengraph": 100}.get(
            self.parser, 0
        )
        return (
            parser_score
            + min(len(self.text), 1000)
            + (100 if self.created_at else 0)
            + (50 if self.author_name else 0)
            + len(self.images) * 25
        )


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def request_with_retries(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 90,
    attempts: int = 5,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code in (408, 425, 429, 500, 502, 503, 504):
                raise requests.HTTPError(
                    f"retryable HTTP {response.status_code}", response=response
                )
            response.raise_for_status()
            return response
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(min(30, 2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def capture_from_row(headers: list[str], row: list[Any]) -> Capture | None:
    item = dict(zip(headers, (str(value) for value in row)))
    match = STATUS_ID_RE.search(item.get("original", ""))
    if not match:
        return None
    return Capture(
        timestamp=item.get("timestamp", ""),
        original=item.get("original", ""),
        mimetype=item.get("mimetype", ""),
        statuscode=item.get("statuscode", ""),
        digest=item.get("digest", ""),
        length=item.get("length", ""),
        tweet_id=match.group(1),
    )


def fetch_manifest(
    username: str,
    manifest_path: Path,
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    captures: dict[str, Capture] = {}
    failures: list[dict[str, str]] = []
    params = {
        "url": f"twitter.com/{username}/status/*",
        "output": "json",
        "fl": "timestamp,original,mimetype,statuscode,digest,length",
        "filter": ["mimetype:text/html", "statuscode:200"],
        # The Wayback URLs view is URL-oriented. Keeping one capture per URL
        # avoids multi-minute CDX timeouts caused by duplicate snapshots while
        # still covering every distinct tweet page that can be downloaded.
        "collapse": "urlkey",
        "limit": "10000",
        "from": str(start_year),
        "to": str(end_year),
    }
    try:
        response = request_with_retries(session, CDX_URL, params=params, timeout=180)
        rows = response.json()
        if isinstance(rows, list) and len(rows) > 1:
            headers = rows[0]
            for row in rows[1:]:
                capture = capture_from_row(headers, row)
                if capture:
                    captures[capture.key] = capture
            print(f"[manifest] {len(rows) - 1} distinct HTTP 200 HTML URLs")
        else:
            print("[manifest] 0 captures")
    except Exception as exc:
        failures.append(
            {"from": str(start_year), "to": str(end_year), "error": str(exc)}
        )
        print(f"[manifest] FAILED {exc}")

    ordered = sorted(captures.values(), key=lambda item: (item.timestamp, item.original))
    manifest = {
        "version": 1,
        "username": username,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query": {
            "mimetype": "text/html",
            "statuscode": "200",
            "collapse": "urlkey",
            "start_year": start_year,
            "end_year": end_year,
        },
        "capture_count": len(ordered),
        "tweet_count": len({item.tweet_id for item in ordered}),
        "failed_intervals": failures,
        "captures": [asdict(item) for item in ordered],
    }
    atomic_json(manifest_path, manifest)
    return manifest


def load_manifest(path: Path) -> tuple[dict[str, Any], list[Capture]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    captures = [Capture(**item) for item in value.get("captures", [])]
    return value, captures


def legacy_path(root: Path, capture: Capture) -> Path:
    return root / capture.tweet_id / f"{capture.timestamp}.html"


def download_captures(
    captures: list[Capture],
    legacy_root: Path,
    status_path: Path,
    workers: int,
    delay: float,
) -> dict[str, Any]:
    legacy_root.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {}
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
    lock = threading.Lock()
    completed_since_save = 0

    def save_status() -> None:
        atomic_json(status_path, status)

    def download_one(capture: Capture) -> tuple[str, dict[str, Any]]:
        target = legacy_path(legacy_root, capture)
        if target.exists() and target.stat().st_size > 0:
            raw = target.read_bytes()
            return capture.key, {
                "status": "downloaded",
                "path": target.as_posix(),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        if delay:
            time.sleep(delay)
        session = requests.Session()
        session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
        url = WAYBACK_URL.format(
            timestamp=capture.timestamp,
            original=capture.original,
        )
        try:
            response = request_with_retries(
                session,
                url,
                timeout=45,
                attempts=3,
            )
            raw = response.content
            if not raw:
                raise ValueError("empty response")
            target.write_bytes(raw)
            return capture.key, {
                "status": "downloaded",
                "path": target.as_posix(),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "final_url": response.url,
            }
        except Exception as exc:
            return capture.key, {
                "status": "failed",
                "path": target.as_posix(),
                "error": f"{type(exc).__name__}: {exc}",
            }

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(download_one, capture): capture for capture in captures}
        for index, future in enumerate(as_completed(futures), 1):
            capture = futures[future]
            try:
                key, result = future.result()
            except Exception as exc:
                key = capture.key
                result = {"status": "failed", "error": f"uncaught: {exc}"}
            with lock:
                status[key] = result
                completed_since_save += 1
                if completed_since_save >= 10:
                    save_status()
                    completed_since_save = 0
            print(
                f"[download {index}/{len(captures)}] {result['status']} "
                f"{capture.timestamp} {capture.tweet_id}"
            )
    save_status()
    return status


def normalize_url(url: str) -> str:
    url = html_module.unescape(url.strip())
    if not url:
        return ""
    parts = urlsplit(url)
    path = re.sub(r":(?:large|small|medium|thumb)$", "", parts.path)
    return urlunsplit((parts.scheme or "https", parts.netloc, path, "", ""))


def unique_media(urls: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in urls:
        url = normalize_url(value)
        if url and MEDIA_URL_RE.search(url) and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def iso_from_tweet_id(tweet_id: str) -> str:
    try:
        milliseconds = (int(tweet_id) >> 22) + TWITTER_EPOCH_MS
        return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (ValueError, OverflowError, OSError):
        return ""


def iso_from_epoch_ms(value: str) -> str:
    try:
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (ValueError, OverflowError, OSError):
        return ""


def cleaned_text(node: Any) -> str:
    if node is None:
        return ""
    text = node.get_text("", strip=False)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_profile_id(url: str) -> str:
    match = PROFILE_ID_RE.search(url or "")
    return match.group(1) if match else ""


def parse_old_dom(soup: BeautifulSoup, capture: Capture, username: str) -> ParsedTweet | None:
    container = soup.find(attrs={"data-tweet-id": capture.tweet_id})
    if container is None:
        return None
    text_node = container.select_one(".tweet-text")
    if text_node is None:
        return None
    text = cleaned_text(text_node)
    if not text:
        return None
    screen_name = container.get("data-screen-name") or username
    name = container.get("data-name") or ""
    user_id = container.get("data-user-id") or ""
    avatar_node = container.select_one("img.js-action-profile-avatar")
    avatar = avatar_node.get("src", "") if avatar_node else ""
    timestamp_node = container.select_one("[data-time-ms]")
    created_at = iso_from_epoch_ms(timestamp_node.get("data-time-ms", "")) if timestamp_node else ""
    created_at = created_at or iso_from_tweet_id(capture.tweet_id)
    conversation_id = container.get("data-conversation-id") or capture.tweet_id
    reply_to_id = ""
    for selector in (
        ".ReplyingToContextBelowAuthor a[href*='/status/']",
        ".js-reply-context a[href*='/status/']",
    ):
        link = container.select_one(selector)
        if link:
            match = STATUS_ID_RE.search(link.get("href", ""))
            if match and match.group(1) != capture.tweet_id:
                reply_to_id = match.group(1)
                break
    images = unique_media(
        [node.get("data-image-url", "") for node in container.select("[data-image-url]")]
        + [node.get("src", "") for node in container.select("img[src*='twimg.com/media/']")]
    )
    return ParsedTweet(
        tweet_id=capture.tweet_id,
        text=text,
        created_at=created_at,
        author_id=user_id or extract_profile_id(avatar),
        author_name=name,
        author_username=screen_name.lstrip("@"),
        author_avatar=normalize_url(avatar),
        conversation_id=conversation_id,
        reply_to_id=reply_to_id,
        images=images,
        capture_timestamp=capture.timestamp,
        original_url=capture.original,
        parser="old-dom",
    )


def meta_content(root: Any, itemprop: str) -> str:
    node = root.find("meta", attrs={"itemprop": itemprop}) if root else None
    return node.get("content", "") if node else ""


def parse_modern_dom(soup: BeautifulSoup, capture: Capture, username: str) -> ParsedTweet | None:
    identifier = soup.find("meta", attrs={"itemprop": "identifier", "content": capture.tweet_id})
    if identifier is None:
        return None
    posting = identifier.find_parent(attrs={"itemtype": re.compile("SocialMediaPosting")})
    if posting is None:
        return None
    article = posting.find("article", attrs={"data-testid": "tweet"})
    text_node = article.find(attrs={"data-testid": "tweetText"}) if article else None
    text = cleaned_text(text_node)
    if not text:
        return None
    author = posting.find(attrs={"itemprop": "author"})
    author_id = meta_content(author, "identifier")
    screen_name = meta_content(author, "additionalName") or username
    name = meta_content(author, "givenName")
    created_at = meta_content(posting, "datePublished") or iso_from_tweet_id(capture.tweet_id)
    avatar = ""
    if article:
        avatar_node = article.find("img", src=re.compile(r"twimg\.com/profile_images/"))
        avatar = avatar_node.get("src", "") if avatar_node else ""
    reply_to_id = ""
    parent_url = meta_content(posting, "isPartOf")
    match = STATUS_ID_RE.search(parent_url)
    if match and match.group(1) != capture.tweet_id:
        reply_to_id = match.group(1)
    images = unique_media(
        [node.get("src", "") for node in (article.find_all("img") if article else [])]
    )
    return ParsedTweet(
        tweet_id=capture.tweet_id,
        text=text,
        created_at=created_at,
        author_id=author_id or extract_profile_id(avatar),
        author_name=name,
        author_username=screen_name.lstrip("@"),
        author_avatar=normalize_url(avatar),
        conversation_id=capture.tweet_id,
        reply_to_id=reply_to_id,
        images=images,
        capture_timestamp=capture.timestamp,
        original_url=capture.original,
        parser="modern-dom",
    )


def parse_opengraph(soup: BeautifulSoup, capture: Capture, username: str) -> ParsedTweet | None:
    description = soup.find("meta", attrs={"property": "og:description"})
    text = description.get("content", "").strip(" \n\r\t\u201c\u201d") if description else ""
    if not text:
        return None
    title_node = soup.find("meta", attrs={"property": "og:title"})
    title = title_node.get("content", "") if title_node else ""
    name = re.sub(r"\s+on Twitter.*$", "", title).strip()
    images = unique_media(
        [node.get("content", "") for node in soup.find_all("meta", attrs={"property": "og:image"})]
    )
    return ParsedTweet(
        tweet_id=capture.tweet_id,
        text=text,
        created_at=iso_from_tweet_id(capture.tweet_id),
        author_id="",
        author_name=name,
        author_username=username,
        author_avatar="",
        conversation_id=capture.tweet_id,
        reply_to_id="",
        images=images,
        capture_timestamp=capture.timestamp,
        original_url=capture.original,
        parser="opengraph",
    )


def parse_capture(path: Path, capture: Capture, username: str) -> ParsedTweet | None:
    soup = BeautifulSoup(path.read_bytes(), "html.parser")
    return (
        parse_old_dom(soup, capture, username)
        or parse_modern_dom(soup, capture, username)
        or parse_opengraph(soup, capture, username)
    )


def parsed_to_v2(tweet: ParsedTweet) -> dict[str, Any]:
    author_id = tweet.author_id or f"legacy-{tweet.author_username.lower()}"
    media: list[dict[str, Any]] = []
    media_keys: list[str] = []
    for index, url in enumerate(tweet.images, 1):
        key = f"legacy_{tweet.tweet_id}_{index}"
        media_keys.append(key)
        media.append({"media_key": key, "type": "photo", "url": url})
    data: dict[str, Any] = {
        "id": tweet.tweet_id,
        "text": tweet.text,
        "created_at": tweet.created_at,
        "author_id": author_id,
        "conversation_id": tweet.conversation_id or tweet.tweet_id,
    }
    if media_keys:
        data["attachments"] = {"media_keys": media_keys}
    if tweet.reply_to_id:
        data["referenced_tweets"] = [{"type": "replied_to", "id": tweet.reply_to_id}]
    return {
        "data": data,
        "includes": {
            "users": [
                {
                    "id": author_id,
                    "name": tweet.author_name,
                    "username": tweet.author_username,
                    "profile_image_url": tweet.author_avatar,
                }
            ],
            "media": media,
        },
        "_legacy_html": {
            "capture_timestamp": tweet.capture_timestamp,
            "original_url": tweet.original_url,
            "parser": tweet.parser,
        },
    }


def find_existing_basename(json_dir: Path, tweet_id: str) -> str | None:
    matches = sorted(json_dir.glob(f"*_status_{tweet_id}.json"))
    return matches[0].stem if matches else None


def local_image_path(image_dir: Path, url: str) -> str:
    match = re.search(
        r"/media/([^/?#]+?)\.(?:jpg|jpeg|png|gif|webp)(?:[?#]|$)",
        url,
        re.I,
    )
    if not match or not image_dir.exists():
        return ""
    basename = match.group(1)
    for path in image_dir.iterdir():
        if path.is_file() and basename in path.name:
            return f"../image/{path.name}"
    return ""


def render_v2_html(value: dict[str, Any], image_dir: Path, fallback_avatar: str) -> str:
    data = value.get("data", {}) or {}
    includes = value.get("includes", {}) or {}
    users = includes.get("users", []) or []
    user = users[0] if users else {}
    media_by_key = {
        item.get("media_key", ""): item for item in includes.get("media", []) or []
    }
    media_keys = (data.get("attachments", {}) or {}).get("media_keys", []) or []
    media_html: list[str] = []
    for key in media_keys:
        item = media_by_key.get(key, {})
        if item.get("type") != "photo":
            continue
        remote = item.get("url", "") or item.get("preview_image_url", "")
        src = local_image_path(image_dir, remote) or remote
        if src:
            media_html.append(
                f'<img class="tweet-image" loading="lazy" src="{html_module.escape(src)}"/>'
            )
    text = html_module.escape(data.get("text", "")).replace("\n", "<br/>\n")
    name = html_module.escape(user.get("name", ""))
    username = html_module.escape(str(user.get("username", "")).lstrip("@"))
    created_at = html_module.escape(data.get("created_at", ""))
    created_at_json = json.dumps(data.get("created_at", ""), ensure_ascii=False)
    tweet_id = html_module.escape(str(data.get("id", "")))
    avatar = fallback_avatar or user.get("profile_image_url", "")
    avatar = html_module.escape(avatar)
    media_block = "\n".join(media_html)
    return f"""<!-- Source: legacy-wayback://status/{tweet_id} -->
<!doctype html>
<html><head><meta charset="utf-8"><style>
body{{margin:0;padding:12px;background:#fff;font-family:Arial,sans-serif;color:#0f1419}}
.tweet-container{{max-width:600px;margin:auto}}.tweet-author{{display:flex;align-items:center;gap:8px}}
.tweet-author-profile-image img{{width:48px;height:48px;border-radius:50%;object-fit:cover}}
.tweet-author-name{{font-weight:700}}.tweet-author-username,.date{{color:#536471}}
.tweet-content{{font-size:20px;line-height:1.45;margin-top:12px;overflow-wrap:anywhere}}
.tweet-image{{display:block;max-width:100%;height:auto;margin-top:12px;border-radius:12px}}
.date{{font-size:13px;margin-top:12px}}
</style></head><body><div class="tweet-container"><div id="nonjsonview">
<div class="tweet-author"><div class="tweet-author-profile-image"><img alt="{name}" src="{avatar}"/></div>
<div class="tweet-author-info"><div class="tweet-author-name">{name}</div>
<div class="tweet-author-username">@{username}</div></div></div>
<div class="tweet-content">{text}
{media_block}
<p class="date"><a id="parentdate">{created_at}</a></p></div>
</div></div>
<script>
var dateString = {created_at_json};
var date = new Date(dateString);
document.querySelector("#parentdate").innerText = date;
</script>
</body></html>
"""


def import_captures(
    username: str,
    captures: list[Capture],
    legacy_root: Path,
    json_dir: Path,
    report_path: Path,
) -> dict[str, Any]:
    best: dict[str, ParsedTweet] = {}
    results: list[dict[str, Any]] = []
    for index, capture in enumerate(captures, 1):
        path = legacy_path(legacy_root, capture)
        if not path.exists():
            results.append({"capture": asdict(capture), "status": "missing"})
            continue
        try:
            parsed = parse_capture(path, capture, username)
        except Exception as exc:
            results.append(
                {"capture": asdict(capture), "status": "parse-error", "error": str(exc)}
            )
            continue
        if parsed is None:
            results.append({"capture": asdict(capture), "status": "unparseable"})
            continue
        current = best.get(parsed.tweet_id)
        if current is None or parsed.score > current.score:
            best[parsed.tweet_id] = parsed
        results.append(
            {
                "capture": asdict(capture),
                "status": "parsed",
                "parser": parsed.parser,
                "score": parsed.score,
                "text_length": len(parsed.text),
                "image_count": len(parsed.images),
            }
        )
        if index % 50 == 0:
            print(f"[import] inspected {index}/{len(captures)} captures")

    json_dir.mkdir(parents=True, exist_ok=True)
    html_dir = json_dir.parent / "html"
    image_dir = json_dir.parent / "image"
    html_dir.mkdir(parents=True, exist_ok=True)
    fallback_avatar = ""
    profile_path = json_dir.parent / "profile.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile_avatar = profile.get("avatar", "")
            if profile_avatar:
                fallback_avatar = f"../{profile_avatar}"
        except (OSError, json.JSONDecodeError):
            pass
    written: list[str] = []
    for tweet_id, parsed in sorted(best.items(), key=lambda item: item[1].created_at):
        basename = find_existing_basename(json_dir, tweet_id)
        if basename is None:
            basename = (
                f"{parsed.capture_timestamp}_twitter_com_{username}_status_{tweet_id}"
            )
        target = json_dir / f"{basename}.json"
        normalized = parsed_to_v2(parsed)
        atomic_json(target, normalized)
        (html_dir / f"{basename}.html").write_text(
            render_v2_html(normalized, image_dir, fallback_avatar),
            encoding="utf-8",
        )
        written.append(target.name)

    report = {
        "version": 1,
        "username": username,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "capture_count": len(captures),
        "parsed_capture_count": sum(item["status"] == "parsed" for item in results),
        "unparseable_capture_count": sum(
            item["status"] == "unparseable" for item in results
        ),
        "missing_capture_count": sum(item["status"] == "missing" for item in results),
        "parse_error_count": sum(item["status"] == "parse-error" for item in results),
        "imported_tweet_count": len(best),
        "parser_counts": {
            parser: sum(
                1 for tweet in best.values() if tweet.parser == parser
            )
            for parser in ("old-dom", "modern-dom", "opengraph")
        },
        "written_json": written,
        "results": results,
    }
    atomic_json(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("manifest", "download", "import", "all"))
    parser.add_argument("--username", required=True)
    parser.add_argument("--account-dir", type=Path, required=True)
    parser.add_argument("--from-year", type=int, default=2006)
    parser.add_argument("--to-year", type=int, default=datetime.now().year)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    account_dir: Path = args.account_dir
    snapshot_dir = account_dir / "wayback_snapshots"
    legacy_root = snapshot_dir / "legacy_html"
    manifest_path = account_dir / "legacy_html_manifest.json"
    status_path = account_dir / "legacy_html_downloads.json"
    report_path = account_dir / "legacy_html_import_report.json"

    if args.command in ("manifest", "all"):
        manifest = fetch_manifest(
            args.username,
            manifest_path,
            args.from_year,
            args.to_year,
        )
        if manifest["failed_intervals"]:
            print(
                f"manifest has {len(manifest['failed_intervals'])} failed intervals; "
                "rerun before publishing"
            )
            if args.command == "all":
                return 1
    if not manifest_path.exists():
        raise SystemExit(f"manifest not found: {manifest_path}")
    _, captures = load_manifest(manifest_path)

    if args.command in ("download", "all"):
        status = download_captures(
            captures,
            legacy_root,
            status_path,
            args.workers,
            args.delay,
        )
        failed = sum(item.get("status") != "downloaded" for item in status.values())
        print(f"download summary: {len(status) - failed} downloaded, {failed} failed")

    if args.command in ("import", "all"):
        report = import_captures(
            args.username,
            captures,
            legacy_root,
            snapshot_dir / "json",
            report_path,
        )
        print(
            f"import summary: {report['imported_tweet_count']} tweets from "
            f"{report['parsed_capture_count']} captures"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
