# Objective

This project aims to build a high quality, manually verified dataset of Singapore Reddit discussions to support research on Singapore specific language understanding. It consists of a Reddit scraper, a structured data pipeline, and an annotation workflow that identifies named entities, Singapore locations, and Singlish expressions. The dataset is intended to serve as a reliable benchmark for evaluating and improving large language models on Singaporean online discourse, with AI generated annotations used only as a first draft before human verification.


## Reddit Scraper

Python CLI scraper for collecting public Reddit threads into human-readable and downstream PII-detection-friendly files.

This version does not require Reddit developer credentials. Live scraping uses Playwright by default to warm up a browser context and fetch Reddit `.json` endpoints through that context. The older `requests` backend is still available with `--fetcher requests`.

The current data-collection focus is `r/singapore`. The scraper saves `structured.json` with empty label arrays, and the separate `annotate.py` helper can later fill those arrays with draft LLM labels for manual review (Note: annotate.py is not being utilized in the current workflow).

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

## Common Scraper Commands

### Quick Default Run

Use `--default` when you want the saved reliable collection preset:

```powershell
python reddit_scraper.py singapore 20 --default
```

The checked-in default preset currently means:

```powershell
--sort top --min-delay 5 --max-delay 10 --cooldown-every-n-posts 5 --cooldown-min 15 --cooldown-max 20 --max-retries 4 --retry-base-delay 10 --resume
```

The values live in:

```text
config/reddit_scraper_defaults.json
```

Edit that JSON file directly, or run the terminal setup wizard:

```powershell
python reddit_scraper.py --configure-defaults
```

Press Enter to keep a value, or type a new value when prompted. CLI flags still win for one run, so this uses the default preset except for `--sort hot`:

```powershell
python reddit_scraper.py singapore 20 --default --sort hot
```

### Custom Reliable Run

Write every setting out when you want the command itself to show the full scrape behavior:

```powershell
python reddit_scraper.py singapore 20 --sort top --min-delay 5 --max-delay 10 --cooldown-every-n-posts 5 --cooldown-min 15 --cooldown-max 20 --max-retries 4 --retry-base-delay 10 --resume
```

Run the same command again to resume later. Resume uses `outputs/scrape_state.json` to skip post IDs that were already saved successfully.

Disable resume for a one-off duplicate-allowed run:

```powershell
python reddit_scraper.py singapore 20 --sort top --no-resume
```

`--delay` still works as the backwards-compatible fixed delay option. If `--min-delay` and `--max-delay` are not provided, the scraper uses `--delay` for both.

### Debug Browser Mode

Use `--headed` to show Chromium while debugging the Playwright fetcher:

```powershell
python reddit_scraper.py singapore 5 --default --headed
```

Use the older requests backend for comparison:

```powershell
python reddit_scraper.py singapore 5 --default --fetcher requests
```

### Offline JSON Modes

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

## Scraper Flags

| Flag | Default | Purpose |
| --- | --- | --- |
| `subreddit` | required for subreddit runs | Subreddit name without `r/`, for example `singapore`. |
| `threads` | required for subreddit runs | Number of listing posts to try collecting. |
| `--default` | off | Load the saved reliable scraper preset from `config/reddit_scraper_defaults.json`. |
| `--configure-defaults` | off | Ask terminal questions and rewrite `config/reddit_scraper_defaults.json`. |
| `--sort` | `hot`, or preset value with `--default` | Reddit listing sort: `hot`, `new`, `top`, or `rising`. |
| `--min-delay` | `--delay`, or preset value with `--default` | Minimum random wait between live thread requests. |
| `--max-delay` | `--delay`, or preset value with `--default` | Maximum random wait between live thread requests. Must be at least `--min-delay`. |
| `--delay` | `1` | Compatibility fixed delay used when no min/max delay is provided. |
| `--cooldown-every-n-posts` | `0`, or preset value with `--default` | After every N successfully processed posts, take a longer cooldown. `0` disables cooldowns. |
| `--cooldown-min` | `0`, or preset value with `--default` | Minimum seconds for the longer cooldown pause. |
| `--cooldown-max` | `0`, or preset value with `--default` | Maximum seconds for the longer cooldown pause. Must be at least `--cooldown-min`. |
| `--max-retries` | `0`, or preset value with `--default` | Number of retries after the first failed fetch attempt. |
| `--retry-base-delay` | `5`, or preset value with `--default` | Base seconds for exponential retry backoff after temporary fetch failures. |
| `--resume` | on | Read and update `outputs/scrape_state.json` so already saved post IDs are skipped on later runs. |
| `--no-resume` | off | Do not read or update resume state; useful for a deliberate duplicate-allowed run. |
| `--fetcher` | `playwright` | Live Reddit JSON backend. Use `requests` only for comparison/debugging. |
| `--headed` | off | Show Chromium while using the Playwright fetcher. |
| `--output-root` | `outputs` | Folder where timestamped run folders and `scrape_state.json` are written. |
| `--url` | none | Scrape one direct Reddit thread URL. Repeat for multiple URLs. |
| `--listing-json-file` | none | Process a browser-saved listing JSON file instead of contacting Reddit live. |
| `--thread-json-file` | none | Process a browser-saved thread JSON file instead of contacting Reddit live. |
| `--self-test` | off | Run offline checks without contacting Reddit. |
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

`labels` is always present but empty:

```json
{"names": [], "locations": [], "singlish": []}
```

`readable.txt` preserves the existing conversation-friendly format, including the `Replying to:` line for every comment.

`pii_input.txt` includes only title, post text, and comment text. It excludes usernames, URLs, scores, and metadata.

## Annotation Helper

`annotate.py` is separate from the scraper. It reads existing `structured.json` files, recursively walks all nested comments and replies, asks a local LM Studio model for draft labels, and saves a copied draft without changing the original `structured.json`.

The helper labels each comment with this schema:

```json
{"names": [], "locations": [], "singlish": []}
```

These are draft labels only. A human reviewer should still check the output against `annotation_guidelines.md` before creating any final dataset.

### LM Studio Setup

Start LM Studio Local Server with Qwen loaded. The helper defaults match this setup:

```text
Base URL: http://127.0.0.1:1234/v1
Model: qwen/qwen3.5-9b
```

The helper uses the OpenAI-compatible chat completions endpoint:

```text
http://127.0.0.1:1234/v1/chat/completions
```

### Test Without Calling the LLM

Use `--dry-run` first to confirm that file discovery, nested comment traversal, and output writing work:

```powershell
python annotate.py outputs\singapore_top_20_20260708_124904\020_andre_low_fadli_fawzi_co-opted_into_wp_cec_faisal_manap_re-appointed_as_vice_chair\structured.json --dry-run --limit 3
```

### Annotate One Thread

```powershell
python annotate.py outputs\singapore_top_20_20260708_124904\020_andre_low_fadli_fawzi_co-opted_into_wp_cec_faisal_manap_re-appointed_as_vice_chair\structured.json --limit 3 --timeout 60
```

This creates:

```text
thread_folder/
  annotation/
    annotated_draft.json
    annotation_log.json
    annotation_errors.json
```

`annotated_draft.json` is the copied JSON with draft labels inserted. `annotation_log.json` records run settings and counts. `annotation_errors.json` records per-comment request, timeout, or response-format failures.

### Annotate a Folder

Pass a folder to recursively find every `structured.json` below it:

```powershell
python annotate.py outputs\singapore_top_20_20260708_124904 --limit 10 --timeout 60
```

For a full run after testing, omit `--limit`:

```powershell
python annotate.py outputs\singapore_top_20_20260708_124904 --timeout 60
```

### Resume and Rerun Behavior

By default, if `annotation/annotated_draft.json` already exists, the helper resumes from that draft and preserves comments that already have non-empty labels. This protects manual edits and avoids relabelling completed comments.

Use `--skip-existing` to skip a thread folder completely when `annotated_draft.json` already exists:

```powershell
python annotate.py outputs\singapore_top_20_20260708_124904 --skip-existing
```

Use `--overwrite-labels` only when you intentionally want to replace existing non-empty draft labels:

```powershell
python annotate.py outputs\singapore_top_20_20260708_124904 --overwrite-labels --limit 10
```

### Common Testing Flags

`--limit` controls how many comments the helper annotates before stopping. For example, `--limit 5` means it will process only 5 comments, which is useful for checking that LM Studio is responding before running a whole thread.

`--timeout` controls how many seconds the helper waits for LM Studio to answer for one comment. For example, `--timeout 60` gives Qwen up to 60 seconds per comment. If the model does not answer in time, that comment is recorded in `annotation_errors.json` and the run continues.

`--dry-run` checks file discovery, comment traversal, and output writing without calling LM Studio. Use this first if you only want to confirm the script can read the input and create the `annotation/` files.

`--skip-existing` skips a thread when `annotation/annotated_draft.json` already exists. Use this for large folders when you do not want to revisit threads that already have draft outputs.

`--overwrite-labels` replaces existing non-empty labels. Leave it off when you want resume-safe behavior that protects previous draft labels or human edits.

### Annotation Flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `input_path` | required | A single `structured.json` file or a folder containing thread outputs. |
| `--output-dir` | none | Optional central output root. If omitted, outputs go into `annotation/` beside each source `structured.json`. |
| `--backend` | `lmstudio` | Annotation backend. Currently only `lmstudio` is supported. |
| `--base-url` | `http://127.0.0.1:1234/v1` | OpenAI-compatible API base URL. |
| `--model` | `qwen/qwen3.5-9b` | Model name shown by LM Studio. |
| `--temperature` | `0` | Sampling temperature. Keep this at `0` for extraction consistency. |
| `--timeout` | `30` | Request timeout in seconds for each comment. Increase this if the local model is slow. |
| `--max-retries` | `2` | Number of retries after the first failed request. |
| `--retry-delay` | `2` | Seconds to wait between retries. |
| `--skip-existing` | off | Skip any file that already has `annotation/annotated_draft.json`. |
| `--overwrite-labels` | off | Replace existing non-empty labels instead of preserving them. |
| `--limit` | none | Maximum number of comments to annotate across the run. Useful for testing. |
| `--dry-run` | off | Walk comments and write output files without calling LM Studio. |

### Safety Rules

- `structured.json` is never overwritten.
- `final_annotated.json` is never created automatically.
- Deleted, removed, empty, URL-only, and GIF-only comments are skipped.
- Invalid model JSON does not crash the run; the comment is left empty and the error is recorded.
- Labels that do not exactly appear in the source comment text are discarded.
- Existing non-empty labels are preserved unless `--overwrite-labels` is used.

## Manual Annotation Merger

`merge_annotations.py` merges labels from an external LLM `output.json` file into a copied `structured.json`. It does not call any LLM and never modifies the original `structured.json`.

For one thread, pass the file pair:

```powershell
python merge_annotations.py path\to\structured.json path\to\output.json
```

For a thread folder that contains both files:

```powershell
python merge_annotations.py path\to\thread_folder
```

For a parent run folder, the script recursively finds thread folders containing both `structured.json` and `output.json`:

```powershell
python merge_annotations.py outputs\some_run_folder
```

The merger writes:

```text
thread_folder/
  annotation/
    refined_structured.json
    refined_merge_log.json
    refined_merge_log.txt
```

If `annotation/refined_structured.json` already exists, the script prints a skip message and does not replace the existing refined file or logs. See `merge_guidelines.md` for the full manual merge workflow and expected `output.json` schema.

## Manual Streamlit Review

Use the Streamlit reviewer after `merge_annotations.py` has created one or more `refined_structured.json` files. The reviewer edits `refined_structured.json` in place and does not edit the source `structured.json`.

```powershell
streamlit run streamlit/streamlit_comment_annotator.py
```

Paste a local path in the sidebar. The path can be a single `refined_structured.json`, one thread folder, or a parent run folder such as:

```text
outputs\singapore_top_20_20260708_124904
```

For parent folders, the reviewer recursively finds `refined_structured.json` files anywhere inside each thread folder, including both direct files and files under `annotation/`. The app shows each thread separately, flattens nested comments for one-by-one review, shows the post and reply chain as collapsible context, and saves the previous comment before moving to the next one.

The label editor has one compact text box each for `names`, `locations`, and `singlish`. Use one label per line; comma-separated labels are also accepted. The first save creates a one-time `.bak` backup beside the refined JSON, then later saves use atomic replacement.

Use **Ship It** in the left Reviewer pane below the filters to export selected reviewed `refined_structured` files into a clean handoff folder. Ship It can export `.json`, `.txt`, or both formats while preserving each thread folder and `annotation/` layout. See [export_guidelines.md](export_guidelines.md) for the full export steps, destination-folder rules, and output examples.

## Offline Check

Run the built-in offline self-test without contacting Reddit:

```powershell
python reddit_scraper.py --self-test
```

