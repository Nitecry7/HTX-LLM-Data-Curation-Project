#!/usr/bin/env python3
"""
Low-volume Playwright diagnostic crawler for Reddit JSON endpoints.

This is intentionally separate from reddit_scraper.py while we test whether
Playwright's browser context request client avoids Reddit 403 responses.
"""

import argparse
import json
import random
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def slugify(text: str, max_len: int = 60) -> str:
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in text)
    slug = "_".join(filter(None, slug.split("_")))
    return slug[:max_len] or "thread"


def pause(a=1.0, b=2.5):
    time.sleep(random.uniform(a, b))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("subreddit")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--sort", default="hot", choices=["hot", "new", "top", "rising"])
    parser.add_argument("--out", default="./reddit_threads")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()

        print("Warming up session...")
        home_resp = page.goto("https://www.reddit.com/", wait_until="domcontentloaded")
        print(f"Home status: {home_resp.status if home_resp else 'none'}")
        pause()

        sub_url = f"https://www.reddit.com/r/{args.subreddit}/{args.sort}/"
        print(f"Visiting r/{args.subreddit} ({args.sort})...")
        resp = page.goto(sub_url, wait_until="domcontentloaded")
        print(f"Subreddit status: {resp.status if resp else 'none'}")
        if resp is None or resp.status >= 400:
            print(f"Failed to load subreddit (status: {resp.status if resp else 'none'}).")
            browser.close()
            return
        pause()

        listing_url = f"https://www.reddit.com/r/{args.subreddit}/{args.sort}.json?limit={args.limit}"
        print(f"Fetching listing JSON: {listing_url}")
        listing_resp = ctx.request.get(listing_url, headers={"Referer": sub_url})
        print(f"Listing fetch status: HTTP {listing_resp.status}")
        if listing_resp.status != 200:
            print(listing_resp.text()[:500])
            browser.close()
            return

        posts = [c["data"] for c in listing_resp.json()["data"]["children"][: args.limit]]

        if not posts:
            print("No posts found.")
            browser.close()
            return

        saved_count = 0
        for i, post in enumerate(posts, start=1):
            title = post.get("title", "untitled")
            permalink = post.get("permalink")
            pid = post.get("id", "unknown")
            filename = f"{i:02d}_{pid}_{slugify(title)}"

            print(f"[{i}/{len(posts)}] {title!r}")
            pause(0.8, 1.8)

            thread_url = f"https://www.reddit.com{permalink.rstrip('/')}.json"
            thread_resp = ctx.request.get(
                thread_url,
                headers={"Referer": f"https://www.reddit.com{permalink}"},
            )
            print(f"    Thread fetch status: HTTP {thread_resp.status}")

            if thread_resp.status != 200:
                print(f"    Failed: HTTP {thread_resp.status}")
                print(f"    {thread_resp.text()[:300]}")
                continue

            out_path = out_dir / f"{filename}.json"
            out_path.write_text(json.dumps(thread_resp.json(), indent=2), encoding="utf-8")
            saved_count += 1
            print(f"    -> {out_path}")

        browser.close()

    print(f"\nDone. Saved {saved_count} file(s) in: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
