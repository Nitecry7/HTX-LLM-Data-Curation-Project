import argparse
import copy
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests


EXPECTED_LABEL_KEYS = ("names", "locations", "singlish")
DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL = "qwen/qwen3.5-9b"


@dataclass
class AnnotationSettings:
    backend: str
    base_url: str
    model: str
    temperature: float
    timeout: float
    max_retries: int
    retry_delay: float
    skip_existing: bool
    overwrite_labels: bool
    limit: int | None
    dry_run: bool


def now_timestamp():
    return datetime.now().isoformat(timespec="seconds")


def log(message):
    print(f"[{now_timestamp()}] {message}")


def empty_labels():
    return {"names": [], "locations": [], "singlish": []}


def labels_are_non_empty(labels):
    if not isinstance(labels, dict):
        return False
    return any(bool(labels.get(key)) for key in EXPECTED_LABEL_KEYS)


def normalize_existing_labels(labels):
    if not isinstance(labels, dict):
        return empty_labels()
    normalized = empty_labels()
    for key in EXPECTED_LABEL_KEYS:
        value = labels.get(key, [])
        if isinstance(value, list):
            normalized[key] = [item for item in value if isinstance(item, str)]
    return normalized


def should_skip_body(body):
    text = (body or "").strip()
    if not text:
        return True
    if text.lower() in {"[deleted]", "[removed]", "deleted", "removed"}:
        return True

    without_gifs = re.sub(r"!\[gif\]\([^)]+\)", "", text, flags=re.IGNORECASE).strip()
    without_links = re.sub(r"\[([^\]]*)\]\(https?://[^)]+\)", r"\1", without_gifs, flags=re.IGNORECASE)
    without_urls = re.sub(r"https?://\S+", "", without_links, flags=re.IGNORECASE).strip()
    return not without_urls


def generated_ref(path):
    return "C" + ".".join(str(index) for index in path)


def iter_comments(comments, path=()):
    if not isinstance(comments, list):
        return
    for index, comment in enumerate(comments, start=1):
        if not isinstance(comment, dict):
            continue
        current_path = path + (index,)
        yield comment, generated_ref(current_path)
        yield from iter_comments(comment.get("replies", []), current_path)


def find_structured_files(input_path):
    path = Path(input_path)
    if path.is_file():
        if path.name != "structured.json":
            raise ValueError(f"Input file must be named structured.json: {path}")
        return [path]
    if not path.exists():
        raise ValueError(f"Input path does not exist: {path}")
    files = sorted(path.rglob("structured.json"))
    if not files:
        raise ValueError(f"No structured.json files found under: {path}")
    return files


def output_paths_for(source_path, input_path, output_dir):
    source_path = Path(source_path)
    input_path = Path(input_path)
    if output_dir is None:
        annotation_dir = source_path.parent / "annotation"
    else:
        output_root = Path(output_dir)
        if input_path.is_dir():
            relative_parent = source_path.parent.relative_to(input_path)
            annotation_dir = output_root / relative_parent / "annotation"
        else:
            annotation_dir = output_root / source_path.parent.name / "annotation"
    return {
        "annotation_dir": annotation_dir,
        "draft": annotation_dir / "annotated_draft.json",
        "log": annotation_dir / "annotation_log.json",
        "errors": annotation_dir / "annotation_errors.json",
    }


def build_prompt(comment_body):
    return (
        "You are annotating Singapore Reddit comments for a research dataset.\n\n"
        "Extract only the following three categories:\n\n"
        "1. names\n"
        "Person names, public figures, organisations, named entities, platforms, or source names mentioned in the comment text.\n\n"
        "2. locations\n"
        "Singapore locations, roads, estates, buildings, malls, MRT stations, neighbourhoods, local abbreviations, or address like references mentioned in the comment text.\n\n"
        "3. singlish\n"
        "Singlish words or phrases in the comment text.\n\n"
        "Return only valid JSON in this exact schema:\n\n"
        "{\n"
        '  "names": [],\n'
        '  "locations": [],\n'
        '  "singlish": []\n'
        "}\n\n"
        "Rules:\n"
        "Only extract text that appears in the comment.\n"
        "Do not infer hidden names or locations.\n"
        "Do not expand abbreviations unless the expanded form appears in the comment.\n"
        "If none are found, return empty arrays.\n"
        "Do not include explanations.\n"
        "Do not include markdown.\n\n"
        f"Comment:\n{comment_body}"
    )


def chat_completions_url(base_url):
    return base_url.rstrip("/") + "/chat/completions"


def request_lmstudio_labels(comment_body, settings):
    payload = {
        "model": settings.model,
        "temperature": settings.temperature,
        "messages": [
            {
                "role": "system",
                "content": "Return only valid JSON. Do not include markdown or explanations.",
            },
            {"role": "user", "content": build_prompt(comment_body)},
        ],
    }
    last_error = None
    for attempt in range(settings.max_retries + 1):
        try:
            response = requests.post(
                chat_completions_url(settings.base_url),
                json=payload,
                timeout=settings.timeout,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            last_error = exc
            if attempt < settings.max_retries:
                time.sleep(settings.retry_delay)
    raise RuntimeError(str(last_error))


def extract_json_object(raw_text):
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def validate_labels(raw_text, comment_body):
    parsed = extract_json_object(raw_text)
    if not isinstance(parsed, dict):
        raise ValueError("model response was not a JSON object")
    if set(parsed.keys()) != set(EXPECTED_LABEL_KEYS):
        raise ValueError(f"model response keys must be exactly {EXPECTED_LABEL_KEYS}")

    labels = empty_labels()
    for key in EXPECTED_LABEL_KEYS:
        value = parsed[key]
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list")
        cleaned = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"{key} contains a non-string value")
            item = item.strip()
            if item and item in comment_body and item not in cleaned:
                cleaned.append(item)
        labels[key] = cleaned
    return labels


def make_error(comment, fallback_ref, error_type, message, raw_model_response=""):
    return {
        "comment_ref": comment.get("comment_ref") or fallback_ref,
        "comment_id": comment.get("comment_id", ""),
        "error_type": error_type,
        "message": message,
        "raw_model_response": raw_model_response,
        "timestamp": now_timestamp(),
    }


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def annotate_file(source_path, paths, settings, remaining_budget):
    started = now_timestamp()
    source_path = Path(source_path)
    paths["annotation_dir"].mkdir(parents=True, exist_ok=True)

    if paths["draft"].exists() and settings.skip_existing:
        log(f"Skipping existing annotation: {paths['draft']}")
        return 0, True

    source_data = load_json(source_path)
    if paths["draft"].exists() and not settings.overwrite_labels:
        draft_data = load_json(paths["draft"])
        log(f"Resuming from existing draft: {paths['draft']}")
    else:
        draft_data = copy.deepcopy(source_data)

    errors = []
    comments = list(iter_comments(draft_data.get("comments", [])))
    total_comments = len(comments)
    annotated = 0
    skipped = 0
    exhausted_limit = False

    for comment, fallback_ref in comments:
        if remaining_budget is not None and remaining_budget <= 0:
            exhausted_limit = True
            break

        body = str(comment.get("body") or "")
        existing_labels = normalize_existing_labels(comment.get("labels"))
        comment["labels"] = existing_labels

        if should_skip_body(body):
            skipped += 1
            continue
        if labels_are_non_empty(existing_labels) and not settings.overwrite_labels:
            skipped += 1
            continue

        if settings.dry_run:
            comment["labels"] = empty_labels()
            annotated += 1
            if remaining_budget is not None:
                remaining_budget -= 1
            continue

        raw_response = ""
        try:
            raw_response = request_lmstudio_labels(body, settings)
            comment["labels"] = validate_labels(raw_response, body)
            annotated += 1
        except Exception as exc:
            comment["labels"] = empty_labels()
            errors.append(make_error(comment, fallback_ref, type(exc).__name__, str(exc), raw_response))
        if remaining_budget is not None:
            remaining_budget -= 1

    completed = now_timestamp()
    log_data = {
        "source_file_path": str(source_path),
        "output_file_path": str(paths["draft"]),
        "backend": settings.backend,
        "base_url": settings.base_url,
        "model": settings.model,
        "started_timestamp": started,
        "completed_timestamp": completed,
        "total_comments_found": total_comments,
        "total_comments_annotated": annotated,
        "total_comments_skipped": skipped,
        "total_errors": len(errors),
        "limit_exhausted": exhausted_limit,
        "settings": {
            "temperature": settings.temperature,
            "timeout": settings.timeout,
            "max_retries": settings.max_retries,
            "retry_delay": settings.retry_delay,
            "skip_existing": settings.skip_existing,
            "overwrite_labels": settings.overwrite_labels,
            "limit": settings.limit,
            "dry_run": settings.dry_run,
        },
    }

    save_json(draft_data, paths["draft"])
    save_json(log_data, paths["log"])
    save_json(errors, paths["errors"])
    log(f"Saved annotation draft: {paths['draft']} ({annotated} annotated, {skipped} skipped, {len(errors)} errors)")
    return annotated, exhausted_limit


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Draft-label Reddit structured.json files with a local LLM.")
    parser.add_argument("input_path", help="A structured.json file or directory containing structured.json files")
    parser.add_argument("--output-dir", help="Optional central output root. Defaults to annotation/ beside each structured.json.")
    parser.add_argument("--backend", default="lmstudio", choices=["lmstudio"], help="Annotation backend")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible API base URL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name served by the backend")
    parser.add_argument("--temperature", type=float, default=0.0, help="LLM temperature")
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds")
    parser.add_argument("--max-retries", type=int, default=2, help="Retry count after the first failed request")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="Seconds to wait between retries")
    parser.add_argument("--skip-existing", action="store_true", help="Skip files where annotated_draft.json already exists")
    parser.add_argument("--overwrite-labels", action="store_true", help="Overwrite existing non-empty labels")
    parser.add_argument("--limit", type=int, help="Maximum number of comments to annotate across this run")
    parser.add_argument("--dry-run", action="store_true", help="Walk files and write outputs without calling the LLM")
    return parser.parse_args(argv)


def build_settings(args):
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than 0")
    if args.max_retries < 0:
        raise ValueError("--max-retries must be 0 or greater")
    if args.retry_delay < 0:
        raise ValueError("--retry-delay must be 0 or greater")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be 1 or greater")
    return AnnotationSettings(
        backend=args.backend,
        base_url=args.base_url,
        model=args.model,
        temperature=args.temperature,
        timeout=args.timeout,
        max_retries=args.max_retries,
        retry_delay=args.retry_delay,
        skip_existing=args.skip_existing,
        overwrite_labels=args.overwrite_labels,
        limit=args.limit,
        dry_run=args.dry_run,
    )


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    try:
        settings = build_settings(args)
        input_path = Path(args.input_path)
        source_files = find_structured_files(input_path)
        log(f"Found {len(source_files)} structured.json file(s)")
        remaining_budget = settings.limit
        for source_file in source_files:
            if remaining_budget is not None and remaining_budget <= 0:
                log("Annotation limit reached; stopping")
                break
            paths = output_paths_for(source_file, input_path, args.output_dir)
            annotated, exhausted = annotate_file(source_file, paths, settings, remaining_budget)
            if remaining_budget is not None:
                remaining_budget -= annotated
            if exhausted:
                log("Annotation limit reached; stopping")
                break
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
