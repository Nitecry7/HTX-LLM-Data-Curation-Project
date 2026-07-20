# Reddit Scraper

Python CLI scraper for collecting public Reddit threads into human-readable and downstream PII-detection-friendly files.

This version does not require Reddit developer credentials. Live scraping uses Playwright by default to warm up a browser context and fetch Reddit `.json` endpoints through that context. The older `requests` backend is still available with `--fetcher requests`.

The current data-collection focus is `r/singapore`. Annotation and LLM labelling are intentionally out of scope for Task 1; future `structured.json` files include empty label arrays only so the data is ready for later manual review.

## Setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Install Playwright Chromium once:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

Optionally set a custom User-Agent for requests:

```powershell
$env:REDDIT_USER_AGENT="HTX Reddit JSON scraper by your_name"
```

## Recommended r/singapore Run

For reliable collection, prefer slow Playwright scraping with random delays, retries, cooldowns, and resume enabled:

```powershell
python reddit_scraper.py singapore 20 --sort hot --min-delay 8 --max-delay 20 --cooldown-every-n-posts 5 --cooldown-min 60 --cooldown-max 180 --max-retries 4 --retry-base-delay 10 --resume
```

Run the same command again to resume later. Resume uses `outputs/scrape_state.json` to skip post IDs that were already saved successfully:

```powershell
python reddit_scraper.py singapore 20 --sort hot --min-delay 8 --max-delay 20 --cooldown-every-n-posts 5 --cooldown-min 60 --cooldown-max 180 --max-retries 4 --retry-base-delay 10 --resume
```

Disable resume for a one-off duplicate-allowed run:

```powershell
python reddit_scraper.py singapore 20 --sort hot --no-resume
```

`--delay` still works as the backwards-compatible fixed delay option. If `--min-delay` and `--max-delay` are not provided, the scraper uses `--delay` for both.

Use `--headed` to show Chromium while debugging:

```powershell
python reddit_scraper.py singapore 5 --sort hot --headed
```

Use the older requests backend for comparison:

```powershell
python reddit_scraper.py singapore 5 --sort hot --fetcher requests
```

## Other Modes

Browser-saved listing JSON mode:

```powershell
python reddit_scraper.py --listing-json-file path\to\listing.json
```

Browser-saved thread JSON mode:

```powershell
python reddit_scraper.py --thread-json-file path\to\thread.json
```

Direct URL mode:

```powershell
python reddit_scraper.py --url "https://www.reddit.com/r/singapore/comments/abc123/example/"
```

Repeat `--url` for multiple threads. Direct URL mode also uses Playwright by default, unless `--fetcher requests` is passed.

## Outputs

Subreddit runs create new timestamped folders like:

```text
outputs/singapore_hot_20_YYYYMMDD_HHMMSS/
```

Direct URL runs create folders like:

```text
outputs/reddit_urls_2_YYYYMMDD_HHMMSS/
```

Each thread folder contains:

```text
readable.txt
structured.json
pii_input.txt
raw_thread.json
```

The run folder also contains:

```text
metadata.json
```

Resume state is stored separately at:

```text
outputs/scrape_state.json
```

Existing completed run folders are not modified or migrated. New fields and resume behavior apply only to future runs.

## Structured JSON Fields

Future `structured.json` files keep the existing nested comment structure and add Reddit metadata where available:

```text
subreddit, sort, thread_index, post_id, post_title, post_url, permalink,
created_utc, title, author, url, score, num_comments, post_text,
comments, unresolved_more_comments, source
```

Each comment includes:

```text
comment_ref, comment_id, parent_id, author, level, replying_to, body,
permalink, score, created_utc, labels, replies
```

`labels` is always present but empty for Task 1:

```json
{"names": [], "locations": [], "singlish": []}
```

`readable.txt` preserves the existing conversation-friendly format, including the `Replying to:` line for every comment.

`pii_input.txt` includes only title, post text, and comment text. It excludes usernames, URLs, scores, and metadata.

## Offline Check

Run the built-in offline self-test without contacting Reddit:

```powershell
python reddit_scraper.py --self-test
```
