import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


EXPECTED_LABEL_KEYS = ("names", "locations", "singlish")
DEFAULT_OUTPUT_NAME = "refined_structured.json"
DEFAULT_LOG_NAME = "refined_merge_log.json"
DEFAULT_TEXT_LOG_NAME = "refined_merge_log.txt"


def now_timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def empty_labels() -> dict[str, list[str]]:
    return {key: [] for key in EXPECTED_LABEL_KEYS}


def log(message: str) -> None:
    print(f"[{now_timestamp()}] {message}")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


def save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def format_text_list(values: Any) -> list[str]:
    if not values:
        return ["  - none"]
    return [f"  - {value}" for value in values]


def format_invalid_items(items: Any) -> list[str]:
    if not items:
        return ["  - none"]
    lines = []
    for item in items:
        if isinstance(item, dict):
            lines.append(f"  - item {item.get('index', '?')}: {item.get('reason', '')}")
        else:
            lines.append(f"  - {item}")
    return lines


def format_text_log(log_data: dict[str, Any]) -> str:
    lines = [
        "Manual Annotation Merge Log",
        "===========================",
        "",
        "Paths",
        "-----",
        f"Structured input: {log_data.get('input_structured_path', '')}",
        f"Annotation input: {log_data.get('input_annotation_path', '')}",
        f"Refined output: {log_data.get('output_path', '')}",
        f"Timestamp: {log_data.get('timestamp', '')}",
        "",
        "Counts",
        "------",
        f"Total comments found: {log_data.get('total_comments_found', 0)}",
        f"Total annotations found: {log_data.get('total_annotations_found', 0)}",
        f"Total comments updated: {log_data.get('total_comments_updated', 0)}",
        f"Total comments without annotation: {log_data.get('total_comments_without_annotation', 0)}",
        f"Total duplicate annotation keys: {log_data.get('total_duplicate_annotation_keys', 0)}",
        "Duplicate annotation keys:",
        *format_text_list(log_data.get("duplicate_annotation_keys", [])),
        "",
        f"Total annotation keys that did not match any comment: "
        f"{log_data.get('total_annotation_keys_that_did_not_match_any_comment', 0)}",
        "Unmatched annotation keys:",
        *format_text_list(log_data.get("unmatched_annotation_keys", [])),
        "",
        "Skipped Invalid Annotation Items",
        "--------------------------------",
        *format_invalid_items(log_data.get("skipped_invalid_annotation_items", [])),
        "",
        "Validation Warnings",
        "-------------------",
        *format_text_list(log_data.get("validation_warnings", [])),
        "",
    ]
    return "\n".join(lines)


def normalize_labels(labels: Any) -> dict[str, list[str]]:
    normalized = empty_labels()
    if not isinstance(labels, dict):
        return normalized

    for key in EXPECTED_LABEL_KEYS:
        value = labels.get(key, [])
        if not isinstance(value, list):
            continue
        normalized[key] = dedupe_preserve_order(
            item.strip() for item in value if isinstance(item, str) and item.strip()
        )
    return normalized


def dedupe_preserve_order(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def merge_label_sets(
    existing_labels: dict[str, list[str]],
    new_labels: dict[str, list[str]],
) -> dict[str, list[str]]:
    merged = empty_labels()
    for key in EXPECTED_LABEL_KEYS:
        merged[key] = dedupe_preserve_order(existing_labels[key] + new_labels[key])
    return merged


def iter_comments(comments: Any) -> Any:
    if not isinstance(comments, list):
        return
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        yield comment
        yield from iter_comments(comment.get("replies", []))


def validate_annotation_item(item: Any, index: int) -> tuple[str, dict[str, list[str]]]:
    if not isinstance(item, dict):
        raise ValueError(f"item {index} must be a dict")
    if "comment_key" not in item:
        raise ValueError(f"item {index} is missing comment_key")
    if "labels" not in item:
        raise ValueError(f"item {index} is missing labels")

    comment_key = item["comment_key"]
    if not isinstance(comment_key, str) or not comment_key.strip():
        raise ValueError(f"item {index} comment_key must be a non-empty string")

    labels = item["labels"]
    if not isinstance(labels, dict):
        raise ValueError(f"item {index} labels must be a dict")
    if set(labels.keys()) != set(EXPECTED_LABEL_KEYS):
        raise ValueError(f"item {index} labels must contain exactly {EXPECTED_LABEL_KEYS}")

    cleaned = empty_labels()
    for key in EXPECTED_LABEL_KEYS:
        value = labels[key]
        if not isinstance(value, list):
            raise ValueError(f"item {index} labels.{key} must be a list")

        cleaned_values: list[str] = []
        for label_index, label in enumerate(value):
            if not isinstance(label, str):
                raise ValueError(f"item {index} labels.{key}[{label_index}] must be a string")
            stripped = label.strip()
            if stripped:
                cleaned_values.append(stripped)
        cleaned[key] = dedupe_preserve_order(cleaned_values)

    return comment_key.strip(), cleaned


def build_annotation_index(raw_annotations: Any) -> tuple[dict[str, dict[str, list[str]]], dict[str, Any]]:
    if not isinstance(raw_annotations, list):
        raise ValueError("output.json root must be a list")

    annotations: dict[str, dict[str, list[str]]] = {}
    duplicate_keys: list[str] = []
    skipped_invalid_items: list[dict[str, Any]] = []
    validation_warnings: list[str] = []

    for index, item in enumerate(raw_annotations, start=1):
        try:
            comment_key, labels = validate_annotation_item(item, index)
        except ValueError as exc:
            skipped_invalid_items.append({"index": index, "reason": str(exc)})
            validation_warnings.append(str(exc))
            continue

        if comment_key in annotations:
            duplicate_keys.append(comment_key)
            validation_warnings.append(f"duplicate annotation key skipped: {comment_key}")
            continue

        annotations[comment_key] = labels

    details = {
        "total_annotations_found": len(raw_annotations),
        "duplicate_annotation_keys": duplicate_keys,
        "skipped_invalid_annotation_items": skipped_invalid_items,
        "validation_warnings": validation_warnings,
    }
    return annotations, details


def label_presence_warnings(
    comment: dict[str, Any],
    comment_key: str,
    labels: dict[str, list[str]],
) -> list[str]:
    body = str(comment.get("body") or "")
    body_lower = body.lower()
    warnings: list[str] = []
    for category in EXPECTED_LABEL_KEYS:
        for label_value in labels[category]:
            if label_value.lower() not in body_lower:
                warnings.append(
                    f"{comment_key}: label not found in comment body: {category} -> {label_value}"
                )
    return warnings


def comment_lookup_key(comment: dict[str, Any], annotations: dict[str, dict[str, list[str]]]) -> str | None:
    comment_id = comment.get("comment_id")
    if isinstance(comment_id, str) and comment_id in annotations:
        return comment_id

    comment_ref = comment.get("comment_ref")
    if isinstance(comment_ref, str) and comment_ref in annotations:
        return comment_ref

    return None


def merge_annotations_into_structured(
    structured_data: Any,
    annotations: dict[str, dict[str, list[str]]],
    log_data: dict[str, Any],
) -> Any:
    refined_data = copy.deepcopy(structured_data)
    comments = list(iter_comments(refined_data.get("comments", [])) if isinstance(refined_data, dict) else [])

    matched_annotation_keys: set[str] = set()
    comments_updated = 0
    comments_without_annotation = 0
    validation_warnings = log_data["validation_warnings"]

    for comment in comments:
        existing_labels = normalize_labels(comment.get("labels"))
        annotation_key = comment_lookup_key(comment, annotations)

        if annotation_key is None:
            if "labels" not in comment:
                comment["labels"] = existing_labels
            comments_without_annotation += 1
            continue

        new_labels = annotations[annotation_key]
        comment["labels"] = merge_label_sets(existing_labels, new_labels)
        matched_annotation_keys.add(annotation_key)
        comments_updated += 1
        validation_warnings.extend(label_presence_warnings(comment, annotation_key, new_labels))

    unmatched_annotation_keys = sorted(set(annotations) - matched_annotation_keys)
    log_data.update(
        {
            "total_comments_found": len(comments),
            "total_comments_updated": comments_updated,
            "total_comments_without_annotation": comments_without_annotation,
            "total_annotation_keys_that_did_not_match_any_comment": len(unmatched_annotation_keys),
            "unmatched_annotation_keys": unmatched_annotation_keys,
        }
    )
    return refined_data


def default_output_path_for(structured_path: Path) -> Path:
    return structured_path.parent / "annotation" / DEFAULT_OUTPUT_NAME


def output_path_for_file_pair(structured_path: Path, output_arg: str | None) -> Path:
    if output_arg is None:
        return default_output_path_for(structured_path)

    output_path = Path(output_arg)
    if output_path.parent == Path("."):
        return structured_path.parent / "annotation" / output_path.name
    return output_path


def find_folder_pairs(input_path: Path) -> list[tuple[Path, Path]]:
    if not input_path.exists():
        raise ValueError(f"Input path does not exist: {input_path}")
    if not input_path.is_dir():
        raise ValueError(f"Folder mode input must be a directory: {input_path}")

    pairs: list[tuple[Path, Path]] = []
    for structured_path in sorted(input_path.rglob("structured.json")):
        annotation_path = structured_path.parent / "output.json"
        if annotation_path.exists():
            pairs.append((structured_path, annotation_path))

    if not pairs:
        raise ValueError(f"No folders containing both structured.json and output.json found under: {input_path}")
    return pairs


def merge_one_pair(structured_path: Path, annotation_path: Path, output_path: Path) -> bool:
    log_path = output_path.parent / DEFAULT_LOG_NAME
    text_log_path = output_path.parent / DEFAULT_TEXT_LOG_NAME
    if output_path.exists():
        log(f"Refined output already exists, skipping without replacement: {output_path}")
        return False

    structured_data = load_json(structured_path)
    raw_annotations = load_json(annotation_path)
    annotations, annotation_details = build_annotation_index(raw_annotations)

    log_data: dict[str, Any] = {
        "input_structured_path": str(structured_path),
        "input_annotation_path": str(annotation_path),
        "output_path": str(output_path),
        "timestamp": now_timestamp(),
        "total_comments_found": 0,
        "total_annotations_found": annotation_details["total_annotations_found"],
        "total_comments_updated": 0,
        "total_comments_without_annotation": 0,
        "total_duplicate_annotation_keys": len(annotation_details["duplicate_annotation_keys"]),
        "duplicate_annotation_keys": annotation_details["duplicate_annotation_keys"],
        "total_annotation_keys_that_did_not_match_any_comment": 0,
        "unmatched_annotation_keys": [],
        "validation_warnings": annotation_details["validation_warnings"],
        "skipped_invalid_annotation_items": annotation_details["skipped_invalid_annotation_items"],
    }

    refined_data = merge_annotations_into_structured(structured_data, annotations, log_data)
    save_json(refined_data, output_path)
    save_json(log_data, log_path)
    save_text(format_text_log(log_data), text_log_path)
    log(f"Saved refined structured JSON: {output_path}")
    log(f"Saved merge JSON log: {log_path}")
    log(f"Saved merge text log: {text_log_path}")
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge external output.json labels into copied Reddit structured.json files."
    )
    parser.add_argument(
        "input_path",
        help="A structured.json file, a thread folder, or a parent folder containing thread folders.",
    )
    parser.add_argument(
        "output_json",
        nargs="?",
        help="Path to output.json when input_path is a structured.json file.",
    )
    parser.add_argument(
        "--output",
        help="Optional refined output filename/path for one file-pair mode. Bare filenames go into annotation/.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        input_path = Path(args.input_path)

        if input_path.is_file():
            if input_path.name != "structured.json":
                raise ValueError(f"Input file must be named structured.json: {input_path}")
            if args.output_json is None:
                raise ValueError("A path to output.json is required when input_path is structured.json")

            annotation_path = Path(args.output_json)
            if not annotation_path.exists():
                raise ValueError(f"output.json path does not exist: {annotation_path}")
            output_path = output_path_for_file_pair(input_path, args.output)
            processed = merge_one_pair(input_path, annotation_path, output_path)
            log(f"Completed: {1 if processed else 0} processed, {0 if processed else 1} skipped")
            return 0

        if args.output_json is not None:
            raise ValueError("Do not pass output_json in folder mode; put output.json beside each structured.json")
        if args.output is not None:
            raise ValueError("--output is only supported in one file-pair mode")

        pairs = find_folder_pairs(input_path)
        processed_count = 0
        skipped_count = 0
        log(f"Found {len(pairs)} folder pair(s)")
        for structured_path, annotation_path in pairs:
            output_path = default_output_path_for(structured_path)
            if merge_one_pair(structured_path, annotation_path, output_path):
                processed_count += 1
            else:
                skipped_count += 1
        log(f"Completed: {processed_count} processed, {skipped_count} skipped")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())



