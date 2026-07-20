import argparse
import html
import json
import os
import random
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


DELETED_AUTHOR = "[deleted]"
DEFAULT_USER_AGENT = "HTX Reddit JSON scraper for data curation"
PLAYWRIGHT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_FOLDER_NAME_LENGTH = 90
REDDIT_BASE_URL = "https://www.reddit.com"


@dataclass
class ScrapeOptions:
    min_delay_seconds: float = 1.0
    max_delay_seconds: float = 1.0
    cooldown_every_n_posts: int = 0
    cooldown_min_seconds: float = 0.0
    cooldown_max_seconds: float = 0.0
    max_retries: int = 0
    retry_base_delay_seconds: float = 5.0
    resume_enabled: bool = True
    state_path: Path | None = None


def log(message):
    timestamp = datetime.now().isoformat(timespec="seconds")
    print(f"[{timestamp}] {message}")


def now_timestamp():
    return datetime.now().isoformat(timespec="seconds")

def sanitize_filename(title, max_length=MAX_FOLDER_NAME_LENGTH):
    cleaned = title.lower()
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"[^a-z0-9_-]", "", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_-")
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_length].rstrip("_-") or "untitled"


def clean_text(value):
    if value is None:
        return ""
    text = str(value)
    for _ in range(2):
        text = html.unescape(text)
    zero_width_chars = ["\u200b", "\u200c", "\u200d", "\ufeff"]
    for char in zero_width_chars:
        text = text.replace(char, "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_author_name(author):
    if not author:
        return DELETED_AUTHOR
    if author == "[deleted]":
        return DELETED_AUTHOR
    return str(author)


def build_listing_json_url(subreddit_name, sort_type, requested_threads, host="www.reddit.com"):
    query = urlencode({"limit": requested_threads, "raw_json": "1"})
    return f"https://{host}/r/{subreddit_name}/{sort_type}.json?{query}"


def build_listing_json_urls(subreddit_name, sort_type, requested_threads):
    return [
        build_listing_json_url(subreddit_name, sort_type, requested_threads, "www.reddit.com"),
        build_listing_json_url(subreddit_name, sort_type, requested_threads, "old.reddit.com"),
    ]


def normalize_thread_json_url(thread_url, host="www.reddit.com"):
    absolute_url = urljoin(REDDIT_BASE_URL, thread_url)
    parsed = urlparse(absolute_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/.json"):
        path = path[:-6] + ".json"
    if not path.endswith(".json"):
        path = f"{path}.json"
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_items.setdefault("raw_json", "1")
    return urlunparse(("https", host, path, "", urlencode(query_items), ""))


def normalize_thread_json_urls(thread_url):
    return [
        normalize_thread_json_url(thread_url, "www.reddit.com"),
        normalize_thread_json_url(thread_url, "old.reddit.com"),
    ]


def strip_json_suffix(thread_url):
    if thread_url.endswith("/.json"):
        return thread_url[:-6] + "/"
    if thread_url.endswith(".json"):
        return thread_url[:-5]
    return thread_url


def request_headers():
    return {"User-Agent": os.getenv("REDDIT_USER_AGENT", DEFAULT_USER_AGENT)}


def is_reddit_block_page(title, body_text):
    combined = f"{title}\n{body_text}".lower()
    block_markers = [
        "reddit - please wait for verification",
        "please wait for verification",
        "checking your browser",
        "blocked for url",
        "verify you are a human",
    ]
    return any(marker in combined for marker in block_markers)


def fetch_json(url, timeout=20):
    import requests
    response = requests.get(url, headers=request_headers(), timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_json_candidates(candidate_urls, fetcher=fetch_json):
    attempts = []
    for candidate_url in candidate_urls:
        try:
            payload = fetcher(candidate_url)
            attempts.append({"url": candidate_url, "ok": True, "error": ""})
            return payload, candidate_url, attempts
        except Exception as exc:
            attempts.append({"url": candidate_url, "ok": False, "error": str(exc)})
    errors = "; ".join(f"{attempt['url']} -> {attempt['error']}" for attempt in attempts)
    raise RuntimeError(f"All JSON candidates failed: {errors}")


class PlaywrightJsonClient:
    def __init__(self, headed=False):
        self.headed = headed
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=not self.headed)
        self.context = self.browser.new_context(
            user_agent=PLAYWRIGHT_USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        self.page = self.context.new_page()
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def warm_page(self, url):
        response = self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        status = response.status if response else None
        if status is None or status >= 400:
            raise RuntimeError(f"Warmup page failed: {url} -> HTTP {status if status is not None else 'none'}")
        return status

    def fetch_json(self, url, referer=""):
        headers = {"Referer": referer} if referer else {}
        try:
            response = self.context.request.get(url, headers=headers, timeout=45000)
            status = response.status
            if status != 200:
                preview = response.text()[:500]
                return None, {"url": url, "ok": False, "status": status, "error": f"HTTP {status}: {preview}"}
            try:
                payload = response.json()
            except Exception as exc:
                preview = response.text()[:500]
                return None, {"url": url, "ok": False, "status": status, "error": f"JSON parse failed: {exc}; preview: {preview}"}
            return payload, {"url": url, "ok": True, "status": status, "error": ""}
        except Exception as exc:
            return None, {"url": url, "ok": False, "status": None, "error": str(exc)}


def fetch_json_candidates_playwright(candidate_urls, client, referer=""):
    attempts = []
    for candidate_url in candidate_urls:
        payload, attempt = client.fetch_json(candidate_url, referer)
        attempts.append(attempt)
        if attempt.get("ok"):
            return payload, candidate_url, attempts
    errors = "; ".join(f"{attempt['url']} -> {attempt['error']}" for attempt in attempts)
    raise RuntimeError(f"All Playwright JSON candidates failed: {errors}")


def fetch_html_fallback(thread_url, thread_index, sort_type):
    import requests
    from bs4 import BeautifulSoup
    response = requests.get(thread_url, headers=request_headers(), timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    title_tag = soup.find("meta", property="og:title") or soup.find("title")
    description_tag = soup.find("meta", property="og:description")
    title = title_tag.get("content") if title_tag and title_tag.has_attr("content") else ""
    if not title and title_tag:
        title = title_tag.get_text(strip=True)
    post_text = description_tag.get("content") if description_tag and description_tag.has_attr("content") else ""
    body_text = soup.get_text(" ", strip=True)
    if is_reddit_block_page(title, body_text):
        raise RuntimeError("Reddit verification/block page returned instead of thread HTML")
    subreddit = extract_subreddit_from_url(thread_url)
    return {
        "subreddit": subreddit,
        "sort": sort_type,
        "thread_index": thread_index,
        "title": title or "untitled",
        "author": DELETED_AUTHOR,
        "url": thread_url,
        "score": None,
        "num_comments": None,
        "post_text": post_text or "",
        "comments": [],
        "unresolved_more_comments": 0,
        "source": "html_fallback",
    }


def extract_subreddit_from_url(thread_url):
    parsed = urlparse(urljoin(REDDIT_BASE_URL, thread_url))
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0].lower() == "r":
        return parts[1]
    return ""


def extract_listing_thread_urls(listing_json):
    children = listing_json.get("data", {}).get("children", [])
    urls = []
    for child in children:
        data = child.get("data", {})
        permalink = data.get("permalink")
        if permalink:
            urls.append(urljoin(REDDIT_BASE_URL, permalink))
    return urls


def extract_post_id_from_url(thread_url):
    parsed = urlparse(urljoin(REDDIT_BASE_URL, thread_url))
    parts = [part for part in parsed.path.split("/") if part]
    if "comments" in parts:
        comments_index = parts.index("comments")
        if len(parts) > comments_index + 1:
            return parts[comments_index + 1]
    return ""


def absolute_permalink(permalink):
    return urljoin(REDDIT_BASE_URL, permalink) if permalink else ""


def empty_labels():
    return {"names": [], "locations": [], "singlish": []}


def state_key(subreddit_name, sort_type):
    return f"{subreddit_name.lower()}|{sort_type}"


def default_scrape_state():
    return {"version": 1, "processed_posts": {}}


def load_scrape_state(state_path):
    if not state_path or not state_path.exists():
        return default_scrape_state()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"Could not read resume state {state_path}: {exc}. Starting with empty state.")
        return default_scrape_state()
    if not isinstance(state, dict):
        return default_scrape_state()
    state.setdefault("version", 1)
    state.setdefault("processed_posts", {})
    return state


def save_scrape_state(state, state_path):
    if not state_path:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(state_path)


def get_processed_post_ids(state, subreddit_name, sort_type):
    bucket = state.get("processed_posts", {}).get(state_key(subreddit_name, sort_type), {})
    return set(bucket.get("ids", []) or [])


def mark_post_processed(state, subreddit_name, sort_type, post_id, thread_url, output_dir):
    key = state_key(subreddit_name, sort_type)
    posts = state.setdefault("processed_posts", {})
    bucket = posts.setdefault(key, {
        "subreddit": subreddit_name,
        "sort": sort_type,
        "ids": [],
        "items": {},
    })
    ids = bucket.setdefault("ids", [])
    if post_id and post_id not in ids:
        ids.append(post_id)
    bucket.setdefault("items", {})[post_id or thread_url] = {
        "post_id": post_id,
        "url": thread_url,
        "output_dir": str(output_dir),
        "saved_at": now_timestamp(),
    }
    bucket["last_processed_post_id"] = post_id
    bucket["last_processed_url"] = thread_url
    bucket["last_successful_scrape_timestamp"] = now_timestamp()


def process_comment(comment_node, level=1, comment_ref="", replying_to="POST"):
    kind = comment_node.get("kind")
    data = comment_node.get("data", {})
    if kind == "more":
        return None, len(data.get("children", []) or [])
    author = get_author_name(data.get("author"))
    replies = []
    unresolved_more_comments = 0
    replies_data = data.get("replies")
    if isinstance(replies_data, dict):
        reply_children = replies_data.get("data", {}).get("children", [])
        for reply_index, reply_node in enumerate(reply_children, start=1):
            reply_ref = f"{comment_ref}.{reply_index}" if comment_ref else str(reply_index)
            reply_target = f"{comment_ref} u/{author}" if comment_ref else f"u/{author}"
            reply, reply_more_count = process_comment(reply_node, level + 1, reply_ref, reply_target)
            unresolved_more_comments += reply_more_count
            if reply:
                replies.append(reply)
    permalink = data.get("permalink")
    return {
        "comment_ref": comment_ref,
        "comment_id": data.get("id", ""),
        "parent_id": data.get("parent_id", ""),
        "author": author,
        "level": level,
        "replying_to": replying_to,
        "body": clean_text(data.get("body")),
        "permalink": absolute_permalink(permalink),
        "score": data.get("score"),
        "created_utc": data.get("created_utc"),
        "labels": empty_labels(),
        "replies": replies,
    }, unresolved_more_comments


def build_thread_data_from_post(post_data, sort_type, thread_index, output_url, comments, unresolved_more_comments, source):
    title = clean_text(post_data.get("title"))
    permalink = post_data.get("permalink")
    return {
        "subreddit": post_data.get("subreddit") or extract_subreddit_from_url(output_url),
        "sort": sort_type,
        "thread_index": thread_index,
        "post_id": post_data.get("id", "") or extract_post_id_from_url(output_url),
        "post_title": title,
        "post_url": output_url,
        "outbound_url": post_data.get("url", ""),
        "permalink": absolute_permalink(permalink) or output_url,
        "created_utc": post_data.get("created_utc"),
        "title": title,
        "author": get_author_name(post_data.get("author")),
        "url": output_url,
        "score": post_data.get("score"),
        "num_comments": post_data.get("num_comments"),
        "post_text": clean_text(post_data.get("selftext")),
        "comments": comments,
        "unresolved_more_comments": unresolved_more_comments,
        "source": source,
    }


def process_listing_post(post_data, sort_type, thread_index, comments_fetch_error=""):
    permalink = post_data.get("permalink")
    output_url = absolute_permalink(permalink) if permalink else post_data.get("url", "")
    thread_data = build_thread_data_from_post(post_data, sort_type, thread_index, output_url, [], 0, "listing_json_post_only")
    thread_data["comments_fetch_error"] = comments_fetch_error
    return thread_data


def process_thread(thread_json, thread_url, sort_type, thread_index):
    post_listing = thread_json[0]
    comment_listing = thread_json[1] if len(thread_json) > 1 else {"data": {"children": []}}
    post_children = post_listing.get("data", {}).get("children", [])
    if not post_children:
        raise ValueError("Thread JSON did not contain post data")
    post_data = post_children[0].get("data", {})
    comments = []
    unresolved_more_comments = 0
    for comment_index, comment_node in enumerate(comment_listing.get("data", {}).get("children", []), start=1):
        comment_ref = f"C{comment_index:03d}"
        comment, more_count = process_comment(comment_node, 1, comment_ref, "POST")
        unresolved_more_comments += more_count
        if comment:
            comments.append(comment)
    permalink = post_data.get("permalink")
    output_url = absolute_permalink(permalink) if permalink else thread_url
    return build_thread_data_from_post(post_data, sort_type, thread_index, output_url, comments, unresolved_more_comments, "reddit_json")


def iter_comment_lines(comments):
    for comment in comments:
        yield comment
        yield from iter_comment_lines(comment.get("replies", []))


def append_readable_comment_lines(lines, comments, parent_comment_id="POST", parent_author="", path_prefix=""):
    for index, comment in enumerate(comments, start=1):
        fallback_id = f"C{index:03d}" if not path_prefix else f"{path_prefix}.{index}"
        comment_id = comment.get("comment_ref") or fallback_id
        level = comment["level"]
        indent = "  " * (level - 1)
        fallback_target = "POST" if parent_comment_id == "POST" else f"{parent_comment_id} u/{parent_author}"
        reply_target = comment.get("replying_to") or fallback_target
        lines.append(f"{indent}[Comment {comment_id} | Level {level} | u/{comment['author']}]")
        lines.append(f"{indent}Replying to: {reply_target}")
        lines.append(f"{indent}{comment['body']}")
        lines.append("")
        append_readable_comment_lines(lines, comment.get("replies", []), comment_id, comment["author"], comment_id)


def save_readable_txt(thread_data, output_path, requested_threads):
    lines = [
        f"SUBREDDIT: r/{thread_data['subreddit']}",
        f"SORT: {thread_data['sort']}",
        f"THREAD: {thread_data['thread_index']} of {requested_threads}",
        "",
        "TITLE:",
        thread_data["title"],
        "",
        "AUTHOR:",
        f"u/{thread_data['author']}",
        "",
        "URL:",
        thread_data["url"],
        "",
        "POST TEXT:",
        thread_data["post_text"],
        "",
        "COMMENTS:",
        "",
    ]
    append_readable_comment_lines(lines, thread_data["comments"])
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def save_structured_json(thread_data, output_path):
    output_path.write_text(json.dumps(thread_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_pii_input_txt(thread_data, output_path):
    chunks = [clean_text(thread_data["title"]), clean_text(thread_data["post_text"])]
    for comment in iter_comment_lines(thread_data["comments"]):
        chunks.append(clean_text(comment["body"]))
    text = "\n\n".join(chunk for chunk in chunks if chunk)
    output_path.write_text(text.rstrip() + "\n", encoding="utf-8")


def save_raw_thread_json(raw_thread_json, output_path):
    output_path.write_text(json.dumps(raw_thread_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_thread_outputs(thread_data, run_output_dir, requested_threads, raw_thread_json=None):
    title_part = sanitize_filename(thread_data["title"])
    folder_name = f"{thread_data['thread_index']:03d}_{title_part}"
    thread_dir = run_output_dir / folder_name
    thread_dir.mkdir(parents=True, exist_ok=True)
    save_readable_txt(thread_data, thread_dir / "readable.txt", requested_threads)
    save_structured_json(thread_data, thread_dir / "structured.json")
    save_pii_input_txt(thread_data, thread_dir / "pii_input.txt")
    if raw_thread_json is not None:
        save_raw_thread_json(raw_thread_json, thread_dir / "raw_thread.json")
    return thread_dir


def random_delay(min_seconds, max_seconds):
    lower = max(0.0, float(min_seconds or 0.0))
    upper = max(lower, float(max_seconds if max_seconds is not None else lower))
    return random.uniform(lower, upper)


def sleep_between_requests(options, reason="Sleeping"):
    if not options:
        return
    seconds = random_delay(options.min_delay_seconds, options.max_delay_seconds)
    if seconds <= 0:
        return
    log(f"{reason} for {seconds:.1f} seconds")
    time.sleep(seconds)


def maybe_cooldown(processed_count, options):
    if not options or not options.cooldown_every_n_posts:
        return
    if processed_count <= 0 or processed_count % options.cooldown_every_n_posts != 0:
        return
    seconds = random_delay(options.cooldown_min_seconds, options.cooldown_max_seconds)
    if seconds <= 0:
        return
    log(f"Cooldown after {processed_count} processed post(s) for {seconds:.1f} seconds")
    time.sleep(seconds)


def with_retries(operation, description, options):
    total_attempts = max(1, int((options.max_retries if options else 0) or 0) + 1)
    last_exc = None
    for attempt in range(1, total_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            last_exc = exc
            if attempt >= total_attempts:
                break
            base_delay = max(0.0, float((options.retry_base_delay_seconds if options else 0.0) or 0.0))
            delay = base_delay * (2 ** (attempt - 1))
            if base_delay:
                delay += random.uniform(0, base_delay)
            log(f"Retrying {description} after error on attempt {attempt}/{total_attempts}: {exc}")
            if delay > 0:
                log(f"Retry backoff sleeping for {delay:.1f} seconds")
                time.sleep(delay)
    raise last_exc

def build_thread_page_url(thread_url):
    absolute_url = urljoin(REDDIT_BASE_URL, thread_url)
    parsed = urlparse(absolute_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/.json"):
        path = path[:-6]
    if path.endswith(".json"):
        path = path[:-5]
    return urlunparse(("https", "www.reddit.com", path + "/", "", "", ""))


def scrape_thread_url_playwright(thread_url, sort_type, thread_index, client, options=None):
    json_urls = normalize_thread_json_urls(thread_url)
    referer = build_thread_page_url(thread_url)
    raw_thread_json, json_url, json_attempts = with_retries(lambda: fetch_json_candidates_playwright(json_urls, client, referer), f"thread {thread_index}", options or ScrapeOptions())
    thread_data = process_thread(raw_thread_json, thread_url, sort_type, thread_index)
    return thread_data, raw_thread_json, json_url, None, json_attempts


def scrape_thread_urls_playwright(thread_urls, sort_type, run_output_dir, requested_threads, client, delay_seconds=1.0, options=None, state=None, subreddit_name=""):
    options = options or ScrapeOptions(delay_seconds, delay_seconds)
    results = []
    errors = []
    scraped_threads = 0
    processed_threads = 0
    unresolved_more_total = 0
    processed_post_ids = get_processed_post_ids(state, subreddit_name, sort_type) if state and options.resume_enabled else set()
    for thread_index, thread_url in enumerate(thread_urls, start=1):
        post_id = extract_post_id_from_url(thread_url)
        if options.resume_enabled and post_id and post_id in processed_post_ids:
            log(f"Skipping already collected post {post_id}: {thread_url}")
            results.append({
                "thread_index": thread_index,
                "source_url": thread_url,
                "post_id": post_id,
                "skipped": True,
                "skip_reason": "already_collected",
            })
            continue
        try:
            log(f"Fetching thread {thread_index} of {len(thread_urls)}: {thread_url}")
            thread_data, raw_thread_json, json_url, json_error, json_attempts = scrape_thread_url_playwright(
                thread_url, sort_type, thread_index, client, options
            )
            thread_dir = save_thread_outputs(thread_data, run_output_dir, requested_threads, raw_thread_json)
            log(f"Saved raw_thread.json, structured.json, readable.txt, and pii_input.txt to {thread_dir}")
            scraped_threads += 1
            processed_threads += 1
            unresolved_more_total += thread_data.get("unresolved_more_comments", 0)
            saved_post_id = thread_data.get("post_id") or post_id
            results.append({
                "thread_index": thread_index,
                "source_url": thread_url,
                "post_id": saved_post_id,
                "json_url": json_url,
                "title": thread_data["title"],
                "source": thread_data.get("source"),
                "output_dir": str(thread_dir),
                "unresolved_more_comments": thread_data.get("unresolved_more_comments", 0),
                "json_error_before_fallback": json_error,
                "json_attempts": json_attempts,
            })
            if state is not None and options.resume_enabled:
                mark_post_processed(state, subreddit_name, sort_type, saved_post_id, thread_url, thread_dir)
                save_scrape_state(state, options.state_path)
                processed_post_ids.add(saved_post_id)
            log(f"Finished thread {thread_index} of {len(thread_urls)}")
        except Exception as exc:
            processed_threads += 1
            log(f"Error collecting thread {thread_index} of {len(thread_urls)}: {exc}")
            errors.append({
                "thread_index": thread_index,
                "source_url": thread_url,
                "post_id": post_id,
                "error": str(exc),
            })
        maybe_cooldown(processed_threads, options)
        if thread_index < len(thread_urls):
            sleep_between_requests(options)
    return scraped_threads, unresolved_more_total, results, errors
def scrape_thread_url(thread_url, sort_type, thread_index, options=None):
    json_urls = normalize_thread_json_urls(thread_url)
    try:
        raw_thread_json, json_url, json_attempts = with_retries(lambda: fetch_json_candidates(json_urls), f"thread {thread_index}", options or ScrapeOptions())
        thread_data = process_thread(raw_thread_json, thread_url, sort_type, thread_index)
        return thread_data, raw_thread_json, json_url, None, json_attempts
    except Exception as json_exc:
        try:
            fallback_url = strip_json_suffix(thread_url)
            thread_data = fetch_html_fallback(fallback_url, thread_index, sort_type)
            return thread_data, None, json_urls[-1], str(json_exc), []
        except Exception as html_exc:
            raise RuntimeError(f"JSON failed: {json_exc}; HTML fallback failed: {html_exc}") from html_exc


def scrape_thread_urls(thread_urls, sort_type, run_output_dir, requested_threads, delay_seconds=1.0, options=None, state=None, subreddit_name=""):
    options = options or ScrapeOptions(delay_seconds, delay_seconds)
    results = []
    errors = []
    scraped_threads = 0
    processed_threads = 0
    unresolved_more_total = 0
    processed_post_ids = get_processed_post_ids(state, subreddit_name, sort_type) if state and options.resume_enabled else set()
    for thread_index, thread_url in enumerate(thread_urls, start=1):
        post_id = extract_post_id_from_url(thread_url)
        if options.resume_enabled and post_id and post_id in processed_post_ids:
            log(f"Skipping already collected post {post_id}: {thread_url}")
            results.append({
                "thread_index": thread_index,
                "source_url": thread_url,
                "post_id": post_id,
                "skipped": True,
                "skip_reason": "already_collected",
            })
            continue
        try:
            log(f"Fetching thread {thread_index} of {len(thread_urls)}: {thread_url}")
            thread_data, raw_thread_json, json_url, json_error, json_attempts = scrape_thread_url(thread_url, sort_type, thread_index, options)
            thread_dir = save_thread_outputs(thread_data, run_output_dir, requested_threads, raw_thread_json)
            log(f"Saved raw_thread.json, structured.json, readable.txt, and pii_input.txt to {thread_dir}")
            scraped_threads += 1
            processed_threads += 1
            unresolved_more_total += thread_data.get("unresolved_more_comments", 0)
            saved_post_id = thread_data.get("post_id") or post_id
            results.append({
                "thread_index": thread_index,
                "source_url": thread_url,
                "post_id": saved_post_id,
                "json_url": json_url,
                "title": thread_data["title"],
                "source": thread_data.get("source"),
                "output_dir": str(thread_dir),
                "unresolved_more_comments": thread_data.get("unresolved_more_comments", 0),
                "json_error_before_fallback": json_error,
                "json_attempts": json_attempts,
            })
            if state is not None and options.resume_enabled:
                mark_post_processed(state, subreddit_name, sort_type, saved_post_id, thread_url, thread_dir)
                save_scrape_state(state, options.state_path)
                processed_post_ids.add(saved_post_id)
            log(f"Finished thread {thread_index} of {len(thread_urls)}")
        except Exception as exc:
            processed_threads += 1
            log(f"Error collecting thread {thread_index} of {len(thread_urls)}: {exc}")
            errors.append({
                "thread_index": thread_index,
                "source_url": thread_url,
                "post_id": post_id,
                "error": str(exc),
            })
        maybe_cooldown(processed_threads, options)
        if thread_index < len(thread_urls):
            sleep_between_requests(options)
    return scraped_threads, unresolved_more_total, results, errors

def scrape_subreddit_playwright(subreddit_name, requested_threads, sort_type="hot", output_root=Path("outputs"), delay_seconds=1.0, headed=False, options=None):
    options = options or ScrapeOptions(delay_seconds, delay_seconds)
    if options.state_path is None:
        options.state_path = output_root / "scrape_state.json"
    state = load_scrape_state(options.state_path) if options.resume_enabled else None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = f"{sanitize_filename(subreddit_name)}_{sort_type}_{requested_threads}_{timestamp}"
    run_output_dir = output_root / run_name
    run_output_dir.mkdir(parents=True, exist_ok=False)
    listing_urls = build_listing_json_urls(subreddit_name, sort_type, requested_threads)
    listing_url = listing_urls[0]
    sub_url = f"https://www.reddit.com/r/{subreddit_name}/{sort_type}/"
    log(f"Starting r/{subreddit_name} ({sort_type}), requesting {requested_threads} thread(s)")
    metadata = {
        "mode": "subreddit",
        "fetcher": "playwright",
        "headed": headed,
        "subreddit": subreddit_name,
        "sort": sort_type,
        "requested_threads": requested_threads,
        "scraped_threads": 0,
        "scraped_at": now_timestamp(),
        "output_directory": str(run_output_dir),
        "listing_url": listing_url,
        "listing_url_candidates": listing_urls,
        "listing_json_url": "",
        "listing_attempts": [],
        "thread_results": [],
        "errors": [],
        "unresolved_more_comments_total": 0,
        "resume_enabled": options.resume_enabled,
        "state_path": str(options.state_path) if options.state_path else "",
        "scrape_options": {
            "min_delay_seconds": options.min_delay_seconds,
            "max_delay_seconds": options.max_delay_seconds,
            "cooldown_every_n_posts": options.cooldown_every_n_posts,
            "cooldown_min_seconds": options.cooldown_min_seconds,
            "cooldown_max_seconds": options.cooldown_max_seconds,
            "max_retries": options.max_retries,
            "retry_base_delay_seconds": options.retry_base_delay_seconds,
        },
    }
    try:
        with PlaywrightJsonClient(headed=headed) as client:
            log("Warming Reddit home page")
            client.warm_page("https://www.reddit.com/")
            log(f"Warming subreddit page: {sub_url}")
            client.warm_page(sub_url)
            log(f"Fetching listing JSON: {listing_url}")
            listing_json, listing_json_url, listing_attempts = with_retries(
                lambda: fetch_json_candidates_playwright(listing_urls, client, sub_url),
                f"r/{subreddit_name} listing",
                options,
            )
            metadata["listing_json_url"] = listing_json_url
            metadata["listing_attempts"] = listing_attempts
            thread_urls = extract_listing_thread_urls(listing_json)[:requested_threads]
            scraped_threads, more_total, results, errors = scrape_thread_urls_playwright(
                thread_urls, sort_type, run_output_dir, requested_threads, client, delay_seconds, options, state, subreddit_name
            )
    except Exception as exc:
        log(f"Stopped with error: {exc}")
        metadata["errors"].append({"listing_url_candidates": listing_urls, "error": str(exc)})
        save_metadata(metadata, run_output_dir / "metadata.json")
        return run_output_dir
    metadata["scraped_threads"] = scraped_threads
    metadata["thread_results"] = results
    metadata["errors"].extend(errors)
    metadata["unresolved_more_comments_total"] = more_total
    save_metadata(metadata, run_output_dir / "metadata.json")
    log(f"Stopped cleanly. Metadata saved to {run_output_dir / 'metadata.json'}")
    return run_output_dir
def scrape_subreddit(subreddit_name, requested_threads, sort_type="hot", output_root=Path("outputs"), delay_seconds=1.0, fetcher="playwright", headed=False, options=None):
    options = options or ScrapeOptions(delay_seconds, delay_seconds)
    if options.state_path is None:
        options.state_path = output_root / "scrape_state.json"
    if fetcher == "playwright":
        return scrape_subreddit_playwright(subreddit_name, requested_threads, sort_type, output_root, delay_seconds, headed, options)
    state = load_scrape_state(options.state_path) if options.resume_enabled else None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = f"{sanitize_filename(subreddit_name)}_{sort_type}_{requested_threads}_{timestamp}"
    run_output_dir = output_root / run_name
    run_output_dir.mkdir(parents=True, exist_ok=False)
    listing_urls = build_listing_json_urls(subreddit_name, sort_type, requested_threads)
    listing_url = listing_urls[0]
    log(f"Starting r/{subreddit_name} ({sort_type}), requesting {requested_threads} thread(s)")
    metadata = {
        "mode": "subreddit",
        "fetcher": "requests",
        "headed": False,
        "subreddit": subreddit_name,
        "sort": sort_type,
        "requested_threads": requested_threads,
        "scraped_threads": 0,
        "scraped_at": now_timestamp(),
        "output_directory": str(run_output_dir),
        "listing_url": listing_url,
        "listing_url_candidates": listing_urls,
        "listing_json_url": "",
        "listing_attempts": [],
        "thread_results": [],
        "errors": [],
        "unresolved_more_comments_total": 0,
        "resume_enabled": options.resume_enabled,
        "state_path": str(options.state_path) if options.state_path else "",
        "scrape_options": {
            "min_delay_seconds": options.min_delay_seconds,
            "max_delay_seconds": options.max_delay_seconds,
            "cooldown_every_n_posts": options.cooldown_every_n_posts,
            "cooldown_min_seconds": options.cooldown_min_seconds,
            "cooldown_max_seconds": options.cooldown_max_seconds,
            "max_retries": options.max_retries,
            "retry_base_delay_seconds": options.retry_base_delay_seconds,
        },
    }
    try:
        log(f"Fetching listing JSON: {listing_url}")
        listing_json, listing_json_url, listing_attempts = with_retries(
            lambda: fetch_json_candidates(listing_urls),
            f"r/{subreddit_name} listing",
            options,
        )
        metadata["listing_json_url"] = listing_json_url
        metadata["listing_attempts"] = listing_attempts
        thread_urls = extract_listing_thread_urls(listing_json)[:requested_threads]
    except Exception as exc:
        log(f"Stopped with error: {exc}")
        metadata["listing_attempts"] = locals().get("listing_attempts", [])
        metadata["errors"].append({"listing_url_candidates": listing_urls, "error": str(exc)})
        save_metadata(metadata, run_output_dir / "metadata.json")
        return run_output_dir
    scraped_threads, more_total, results, errors = scrape_thread_urls(
        thread_urls, sort_type, run_output_dir, requested_threads, delay_seconds, options, options, state, subreddit_name
    )
    metadata["scraped_threads"] = scraped_threads
    metadata["thread_results"] = results
    metadata["errors"].extend(errors)
    metadata["unresolved_more_comments_total"] = more_total
    save_metadata(metadata, run_output_dir / "metadata.json")
    log(f"Stopped cleanly. Metadata saved to {run_output_dir / 'metadata.json'}")
    return run_output_dir

def scrape_thread_json_file(thread_json_file, output_root=Path("outputs")):
    thread_path = Path(thread_json_file)
    thread_json = json.loads(thread_path.read_text(encoding="utf-8"))
    validate_thread_json_shape(thread_json)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = f"thread_json_files_1_{timestamp}"
    run_output_dir = output_root / run_name
    run_output_dir.mkdir(parents=True, exist_ok=False)
    thread_data = process_thread(thread_json, str(thread_path), "thread_file", 1)
    save_thread_outputs(thread_data, run_output_dir, 1, thread_json)
    metadata = {
        "mode": "thread_json_file",
        "thread_json_file": str(thread_path),
        "requested_threads": 1,
        "scraped_threads": 1,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "output_directory": str(run_output_dir),
        "thread_results": [{
            "thread_index": 1,
            "source_file": str(thread_path),
            "title": thread_data["title"],
            "source": thread_data.get("source"),
            "unresolved_more_comments": thread_data.get("unresolved_more_comments", 0),
        }],
        "errors": [],
        "unresolved_more_comments_total": thread_data.get("unresolved_more_comments", 0),
    }
    save_metadata(metadata, run_output_dir / "metadata.json")
    return run_output_dir


def scrape_listing_json_file(listing_json_file, output_root=Path("outputs"), delay_seconds=1.0):
    listing_path = Path(listing_json_file)
    listing_json = json.loads(listing_path.read_text(encoding="utf-8"))
    validate_listing_json_shape(listing_json)
    children = listing_json.get("data", {}).get("children", [])
    requested_threads = len(children)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = f"{sanitize_filename(listing_path.stem)}_{requested_threads}_{timestamp}"
    run_output_dir = output_root / run_name
    run_output_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "mode": "listing_json_file",
        "listing_json_file": str(listing_path),
        "requested_threads": requested_threads,
        "scraped_threads": 0,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "output_directory": str(run_output_dir),
        "thread_results": [],
        "errors": [],
        "post_only_threads": [],
        "unresolved_more_comments_total": 0,
    }
    unresolved_more_total = 0
    for thread_index, child in enumerate(children, start=1):
        post_data = child.get("data", {})
        permalink = post_data.get("permalink")
        thread_url = urljoin(REDDIT_BASE_URL, permalink) if permalink else post_data.get("url", "")
        try:
            thread_data, raw_thread_json, json_url, json_error, json_attempts = scrape_thread_url(thread_url, "listing_file", thread_index)
            save_thread_outputs(thread_data, run_output_dir, requested_threads, raw_thread_json)
            metadata["scraped_threads"] += 1
            unresolved_more_total += thread_data.get("unresolved_more_comments", 0)
            metadata["thread_results"].append({
                "thread_index": thread_index,
                "source_url": thread_url,
                "json_url": json_url,
                "title": thread_data["title"],
                "source": thread_data.get("source"),
                "unresolved_more_comments": thread_data.get("unresolved_more_comments", 0),
                "json_error_before_fallback": json_error,
                "json_attempts": json_attempts,
            })
        except Exception as exc:
            comments_fetch_error = str(exc)
            thread_data = process_listing_post(post_data, "listing_file", thread_index, comments_fetch_error)
            save_thread_outputs(thread_data, run_output_dir, requested_threads)
            metadata["scraped_threads"] += 1
            metadata["post_only_threads"].append({
                "thread_index": thread_index,
                "source_url": thread_url,
                "title": thread_data["title"],
                "comments_fetch_error": comments_fetch_error,
            })
            metadata["thread_results"].append({
                "thread_index": thread_index,
                "source_url": thread_url,
                "title": thread_data["title"],
                "source": thread_data.get("source"),
                "comments_fetch_error": comments_fetch_error,
            })
        if thread_index < requested_threads and delay_seconds:
            time.sleep(delay_seconds)
    metadata["unresolved_more_comments_total"] = unresolved_more_total
    save_metadata(metadata, run_output_dir / "metadata.json")
    return run_output_dir


def scrape_urls_playwright(thread_urls, sort_type="url", output_root=Path("outputs"), delay_seconds=1.0, headed=False, options=None):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    requested_threads = len(thread_urls)
    run_name = f"reddit_urls_{requested_threads}_{timestamp}"
    run_output_dir = output_root / run_name
    run_output_dir.mkdir(parents=True, exist_ok=False)
    try:
        with PlaywrightJsonClient(headed=headed) as client:
            client.warm_page("https://www.reddit.com/")
            scraped_threads, more_total, results, errors = scrape_thread_urls_playwright(
                thread_urls, sort_type, run_output_dir, requested_threads, client, delay_seconds, options
            )
    except Exception as exc:
        scraped_threads = 0
        more_total = 0
        results = []
        errors = [{"error": str(exc)}]
    metadata = {
        "mode": "urls",
        "fetcher": "playwright",
        "headed": headed,
        "sort": sort_type,
        "requested_threads": requested_threads,
        "scraped_threads": scraped_threads,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "output_directory": str(run_output_dir),
        "direct_urls": thread_urls,
        "thread_results": results,
        "errors": errors,
        "unresolved_more_comments_total": more_total,
    }
    save_metadata(metadata, run_output_dir / "metadata.json")
    return run_output_dir

def scrape_urls(thread_urls, sort_type="url", output_root=Path("outputs"), delay_seconds=1.0, fetcher="playwright", headed=False, options=None):
    if fetcher == "playwright":
        return scrape_urls_playwright(thread_urls, sort_type, output_root, delay_seconds, headed, options)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    requested_threads = len(thread_urls)
    run_name = f"reddit_urls_{requested_threads}_{timestamp}"
    run_output_dir = output_root / run_name
    run_output_dir.mkdir(parents=True, exist_ok=False)
    scraped_threads, more_total, results, errors = scrape_thread_urls(
        thread_urls, sort_type, run_output_dir, requested_threads, delay_seconds, options
    )
    metadata = {
        "mode": "urls",
        "fetcher": "requests",
        "headed": False,
        "sort": sort_type,
        "requested_threads": requested_threads,
        "scraped_threads": scraped_threads,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "output_directory": str(run_output_dir),
        "direct_urls": thread_urls,
        "thread_results": results,
        "errors": errors,
        "unresolved_more_comments_total": more_total,
    }
    save_metadata(metadata, run_output_dir / "metadata.json")
    return run_output_dir


def save_metadata(metadata, output_path):
    output_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_thread_json_shape(payload):
    return isinstance(payload, list) and len(payload) >= 1 and all(isinstance(item, dict) for item in payload)


def is_listing_json_shape(payload):
    return isinstance(payload, dict) and payload.get("kind") == "Listing" and isinstance(payload.get("data"), dict)


def validate_listing_json_shape(payload):
    if is_thread_json_shape(payload):
        raise ValueError("This looks like a thread JSON file. Use --thread-json-file instead.")
    if not is_listing_json_shape(payload):
        raise ValueError("Expected a Reddit listing JSON object with kind='Listing' and data.children.")


def validate_thread_json_shape(payload):
    if is_listing_json_shape(payload):
        raise ValueError("This looks like a listing JSON file. Use --listing-json-file instead.")
    if not is_thread_json_shape(payload):
        raise ValueError("Expected a Reddit thread JSON array containing post and comment listings.")


def fixture_thread_json():
    return [
        {
            "kind": "Listing",
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "subreddit": "singapore",
                            "id": "abc123",
                            "title": "Where can I find affordable rental near Tampines? / test",
                            "author": "example_user",
                            "permalink": "/r/singapore/comments/abc123/example/",
                            "created_utc": 1767225600.0,
                            "score": 123,
                            "num_comments": 3,
                            "selftext": "Budget is around $900.",
                        },
                    }
                ]
            },
        },
        {
            "kind": "Listing",
            "data": {
                "children": [
                    {
                        "kind": "t1",
                        "data": {
                            "id": "c1",
                            "parent_id": "t3_abc123",
                            "author": "commenter_one",
                            "body": "Try Simei.",
                            "permalink": "/r/singapore/comments/abc123/example/c1/",
                            "score": 7,
                            "created_utc": 1767225700.0,
                            "replies": {
                                "kind": "Listing",
                                "data": {
                                    "children": [
                                        {
                                            "kind": "t1",
                                            "data": {
                                                "id": "c2",
                                                "parent_id": "t1_c1",
                                                "author": "[deleted]",
                                                "body": "Bedok may work too.",
                                                "permalink": "/r/singapore/comments/abc123/example/c2/",
                                                "score": 3,
                                                "created_utc": 1767225800.0,
                                                "replies": "",
                                            },
                                        },
                                        {"kind": "more", "data": {"children": ["def456"]}},
                                    ]
                                },
                            },
                        },
                    }
                ]
            },
        },
    ]


def run_self_test():
    listing = {
        "kind": "Listing",
        "data": {
            "children": [
                {"data": {"permalink": "/r/singapore/comments/abc123/example/"}},
                {"data": {"permalink": "/r/singapore/comments/def456/example_two/"}},
            ]
        }
    }
    assert clean_text("hello &amp;#x200B; world") == "hello  world"
    assert clean_text("A&#x200B;B") == "AB"
    urls = extract_listing_thread_urls(listing)
    assert urls[0] == "https://www.reddit.com/r/singapore/comments/abc123/example/"
    assert build_listing_json_urls("singapore", "hot", 5)[1] == "https://old.reddit.com/r/singapore/hot.json?limit=5&raw_json=1"
    assert normalize_thread_json_url(urls[0]) == "https://www.reddit.com/r/singapore/comments/abc123/example.json?raw_json=1"
    assert normalize_thread_json_url(urls[0] + ".json").endswith("/example.json?raw_json=1")
    assert normalize_thread_json_urls(urls[0])[1] == "https://old.reddit.com/r/singapore/comments/abc123/example.json?raw_json=1"
    assert is_reddit_block_page("Reddit - Please wait for verification", "") is True
    try:
        validate_listing_json_shape(fixture_thread_json())
        raise AssertionError("thread JSON should not validate as listing JSON")
    except ValueError as exc:
        assert "--thread-json-file" in str(exc)
    try:
        validate_thread_json_shape(listing)
        raise AssertionError("listing JSON should not validate as thread JSON")
    except ValueError as exc:
        assert "--listing-json-file" in str(exc)
    listing_post = process_listing_post(fixture_thread_json()[0]["data"]["children"][0]["data"], "listing_file", 1, "blocked")
    assert listing_post["source"] == "listing_json_post_only"
    assert listing_post["comments"] == []
    assert listing_post["comments_fetch_error"] == "blocked"
    def fake_fetcher(candidate_url):
        if "www.reddit.com" in candidate_url:
            raise RuntimeError("403 Client Error: Blocked")
        return listing
    candidate_payload, candidate_url, candidate_attempts = fetch_json_candidates(
        build_listing_json_urls("singapore", "hot", 5), fake_fetcher
    )
    assert candidate_payload == listing
    assert candidate_url.startswith("https://old.reddit.com/")
    assert candidate_attempts[0]["ok"] is False
    assert candidate_attempts[1]["ok"] is True
    sample = process_thread(fixture_thread_json(), urls[0], "hot", 1)
    assert sample["post_id"] == "abc123"
    assert sample["post_title"] == sample["title"]
    assert sample["permalink"] == urls[0]
    assert sample["created_utc"] == 1767225600.0
    assert sample["comments"][0]["comment_ref"] == "C001"
    assert sample["comments"][0]["comment_id"] == "c1"
    assert sample["comments"][0]["parent_id"] == "t3_abc123"
    assert sample["comments"][0]["replying_to"] == "POST"
    assert sample["comments"][0]["labels"] == empty_labels()
    assert sample["comments"][0]["replies"][0]["level"] == 2
    assert sample["comments"][0]["replies"][0]["comment_ref"] == "C001.1"
    assert sample["comments"][0]["replies"][0]["replying_to"] == "C001 u/commenter_one"
    assert sample["comments"][0]["replies"][0]["author"] == DELETED_AUTHOR
    assert sample["unresolved_more_comments"] == 1
    temp_dir = Path("_self_test_outputs")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(exist_ok=True)
    fixture_path = temp_dir / "thread_fixture.json"
    fixture_path.write_text(json.dumps(fixture_thread_json(), ensure_ascii=False), encoding="utf-8")
    thread_run_dir = scrape_thread_json_file(fixture_path, temp_dir / "runs")
    assert (thread_run_dir / "metadata.json").exists()
    thread_dir = save_thread_outputs(sample, temp_dir, 1, fixture_thread_json())
    structured = json.loads((thread_dir / "structured.json").read_text(encoding="utf-8"))
    raw_thread = json.loads((thread_dir / "raw_thread.json").read_text(encoding="utf-8"))
    pii_text = (thread_dir / "pii_input.txt").read_text(encoding="utf-8")
    state_path = temp_dir / "scrape_state.json"
    state = default_scrape_state()
    mark_post_processed(state, "singapore", "hot", "abc123", urls[0], thread_dir)
    save_scrape_state(state, state_path)
    loaded_state = load_scrape_state(state_path)
    assert "abc123" in get_processed_post_ids(loaded_state, "singapore", "hot")
    readable_text = (thread_dir / "readable.txt").read_text(encoding="utf-8")
    assert "[Comment C001 | Level 1 | u/commenter_one]" in readable_text
    assert "Replying to: C001 u/commenter_one" in readable_text
    shutil.rmtree(temp_dir)
    assert structured["comments"][0]["replies"][0]["body"] == "Bedok may work too."
    assert raw_thread[0]["data"]["children"][0]["data"]["title"] == sample["title"]
    assert "example_user" not in pii_text
    assert "https://reddit.com" not in pii_text
    print("Self-test passed")


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Scrape Reddit threads into readable, structured, and PII-input files.")
    parser.add_argument("subreddit", nargs="?", help="Subreddit name, for example singapore or askSingapore")
    parser.add_argument("threads", nargs="?", type=int, help="Number of threads to scrape")
    parser.add_argument("--sort", default="hot", choices=["hot", "new", "top", "rising"], help="Subreddit sort type")
    parser.add_argument("--url", action="append", default=[], help="Direct Reddit thread URL. Repeat for multiple URLs.")
    parser.add_argument("--listing-json-file", help="Browser-saved Reddit listing JSON file, for example hot.json")
    parser.add_argument("--thread-json-file", help="Browser-saved Reddit thread JSON file with post and comments")
    parser.add_argument("--delay", type=float, default=1.0, help="Compatibility option: fixed seconds to wait between thread requests")
    parser.add_argument("--min-delay", type=float, help="Minimum random delay between live thread requests")
    parser.add_argument("--max-delay", type=float, help="Maximum random delay between live thread requests")
    parser.add_argument("--cooldown-every-n-posts", type=int, default=0, help="Take a longer cooldown after every N fetched posts; 0 disables cooldown")
    parser.add_argument("--cooldown-min", type=float, default=0.0, help="Minimum cooldown seconds")
    parser.add_argument("--cooldown-max", type=float, default=0.0, help="Maximum cooldown seconds")
    parser.add_argument("--max-retries", type=int, default=0, help="Retry count after the first failed attempt for temporary fetch failures")
    parser.add_argument("--retry-base-delay", type=float, default=5.0, help="Base seconds for exponential retry backoff")
    parser.add_argument("--resume", dest="resume", action="store_true", help="Use outputs/scrape_state.json to skip posts already collected")
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="Do not read or update resume state")
    parser.set_defaults(resume=True)
    parser.add_argument("--fetcher", default="playwright", choices=["playwright", "requests"], help="Live Reddit JSON fetch backend")
    parser.add_argument("--headed", action="store_true", help="Show Chromium when using the Playwright fetcher")
    parser.add_argument("--output-root", default="outputs", help="Directory where scrape outputs will be created")
    parser.add_argument("--self-test", action="store_true", help="Run offline checks without contacting Reddit")
    return parser.parse_args(argv)


def build_scrape_options(args):
    min_delay = args.delay if args.min_delay is None else args.min_delay
    max_delay = args.delay if args.max_delay is None else args.max_delay
    if min_delay < 0 or max_delay < 0:
        raise ValueError("delay values must be 0 or greater")
    if max_delay < min_delay:
        raise ValueError("--max-delay must be greater than or equal to --min-delay")
    if args.cooldown_every_n_posts < 0:
        raise ValueError("--cooldown-every-n-posts must be 0 or greater")
    if args.cooldown_min < 0 or args.cooldown_max < 0:
        raise ValueError("cooldown values must be 0 or greater")
    if args.cooldown_max < args.cooldown_min:
        raise ValueError("--cooldown-max must be greater than or equal to --cooldown-min")
    if args.max_retries < 0:
        raise ValueError("--max-retries must be 0 or greater")
    if args.retry_base_delay < 0:
        raise ValueError("--retry-base-delay must be 0 or greater")
    output_root = Path(args.output_root)
    return ScrapeOptions(
        min_delay_seconds=min_delay,
        max_delay_seconds=max_delay,
        cooldown_every_n_posts=args.cooldown_every_n_posts,
        cooldown_min_seconds=args.cooldown_min,
        cooldown_max_seconds=args.cooldown_max,
        max_retries=args.max_retries,
        retry_base_delay_seconds=args.retry_base_delay,
        resume_enabled=args.resume,
        state_path=output_root / "scrape_state.json",
    )


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    if args.self_test:
        run_self_test()
        return 0
    try:
        options = build_scrape_options(args)
        if args.thread_json_file:
            output_dir = scrape_thread_json_file(args.thread_json_file, Path(args.output_root))
        elif args.listing_json_file:
            output_dir = scrape_listing_json_file(args.listing_json_file, Path(args.output_root), args.delay)
        elif args.url:
            output_dir = scrape_urls(args.url, "url", Path(args.output_root), args.delay, args.fetcher, args.headed, options)
        else:
            if not args.subreddit or args.threads is None:
                print("subreddit and threads are required unless --url, --listing-json-file, --thread-json-file, or --self-test is used", file=sys.stderr)
                return 2
            if args.threads < 1:
                print("threads must be 1 or greater", file=sys.stderr)
                return 2
            output_dir = scrape_subreddit(args.subreddit, args.threads, args.sort, Path(args.output_root), args.delay, args.fetcher, args.headed, options)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Scrape complete: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



















