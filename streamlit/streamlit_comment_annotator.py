import copy
import html
import json
import os
import shutil
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st


EXPECTED_LABEL_KEYS = ("names", "locations", "singlish")
APP_NAME = "streamlit_comment_annotator"
REFINED_JSON_NAME = "refined_structured.json"
SOURCE_JSON_NAME = "structured.json"


@dataclass
class ThreadEntry:
    thread_folder: Path
    candidates: list[Path]
    refined_path: Path | None
    title: str
    missing_refined: bool = False


@dataclass
class ReviewItem:
    comment: dict[str, Any]
    fallback_ref: str
    ancestors: list[dict[str, Any]]


def empty_labels() -> dict[str, list[str]]:
    return {key: [] for key in EXPECTED_LABEL_KEYS}


def now_timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def backup_path_for(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def save_json_atomic(data: Any, path: Path) -> None:
    path = Path(path)
    backup_path = backup_path_for(path)
    if path.exists() and not backup_path.exists():
        backup_path.write_bytes(path.read_bytes())

    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
        os.replace(temp_name, path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()


def normalize_labels(labels: Any) -> dict[str, list[str]]:
    normalized = empty_labels()
    if not isinstance(labels, dict):
        return normalized

    for key in EXPECTED_LABEL_KEYS:
        value = labels.get(key, [])
        if isinstance(value, list):
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


def parse_label_text(text: str) -> list[str]:
    parts: list[str] = []
    for line in (text or "").splitlines():
        parts.extend(line.split(","))
    return dedupe_preserve_order(part.strip() for part in parts if part.strip())


def labels_to_text(labels: dict[str, list[str]], key: str) -> str:
    return "\n".join(labels.get(key, []))


def labels_changed(before: Any, after: dict[str, list[str]]) -> bool:
    return normalize_labels(before) != normalize_labels(after)


HIGHLIGHT_STYLES = {
    "names": "background-color: #ffe58a; color: #1f1f1f; padding: 0 0.15rem; border-radius: 0.2rem;",
    "locations": "background-color: #9fd8ff; color: #102033; padding: 0 0.15rem; border-radius: 0.2rem;",
    "singlish": "background-color: #a8f0bf; color: #102414; padding: 0 0.15rem; border-radius: 0.2rem;",
}


def review_progress(items: list[ReviewItem]) -> tuple[int, int, float]:
    total = len(items)
    reviewed = sum(1 for item in items if is_reviewed(item.comment))
    ratio = reviewed / total if total else 0.0
    return reviewed, total, ratio


def label_values_for_highlight(labels: dict[str, list[str]]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for category in EXPECTED_LABEL_KEYS:
        for value in labels.get(category, []):
            if value:
                values.append((category, value))
    return values


def find_label_matches(body: str, labels: dict[str, list[str]]) -> tuple[list[tuple[int, int, str]], dict[str, list[str]]]:
    candidates: list[tuple[int, int, str, str]] = []
    unmatched = empty_labels()
    for category, label in label_values_for_highlight(labels):
        starts = [match.start() for match in re.finditer(re.escape(label), body)]
        if not starts:
            unmatched[category].append(label)
            continue
        for start in starts:
            candidates.append((start, start + len(label), category, label))

    candidates.sort(key=lambda item: (-(item[1] - item[0]), item[0], item[3]))
    selected: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for start, end, category, _label in candidates:
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        selected.append((start, end, category))
        occupied.append((start, end))

    selected.sort(key=lambda item: item[0])
    return selected, unmatched


def highlighted_comment_html(body: str, labels: dict[str, list[str]]) -> tuple[str, dict[str, list[str]]]:
    matches, unmatched = find_label_matches(body, labels)
    parts: list[str] = []
    cursor = 0
    for start, end, category in matches:
        parts.append(html.escape(body[cursor:start]))
        style = HIGHLIGHT_STYLES[category]
        parts.append(f'<mark style="{style}">{html.escape(body[start:end])}</mark>')
        cursor = end
    parts.append(html.escape(body[cursor:]))
    rendered = "".join(parts).replace("\n", "<br>")
    return rendered, unmatched


def unmatched_label_text(unmatched: dict[str, list[str]]) -> str:
    parts = []
    for category in EXPECTED_LABEL_KEYS:
        values = unmatched.get(category, [])
        if values:
            parts.append(f"{category}: {', '.join(values)}")
    return " | ".join(parts)


def generated_ref(path: tuple[int, ...]) -> str:
    if not path:
        return "C000"
    first = f"C{path[0]:03d}"
    if len(path) == 1:
        return first
    return ".".join([first, *[str(index) for index in path[1:]]])


def iter_review_items(
    comments: Any,
    path: tuple[int, ...] = (),
    ancestors: list[dict[str, Any]] | None = None,
) -> list[ReviewItem]:
    if ancestors is None:
        ancestors = []
    if not isinstance(comments, list):
        return []

    items: list[ReviewItem] = []
    for index, comment in enumerate(comments, start=1):
        if not isinstance(comment, dict):
            continue
        current_path = path + (index,)
        fallback_ref = generated_ref(current_path)
        items.append(ReviewItem(comment=comment, fallback_ref=fallback_ref, ancestors=list(ancestors)))
        items.extend(iter_review_items(comment.get("replies", []), current_path, ancestors + [comment]))
    return items


def comment_ref(comment: dict[str, Any], fallback_ref: str = "") -> str:
    value = comment.get("comment_ref")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback_ref


def comment_id(comment: dict[str, Any]) -> str:
    value = comment.get("comment_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def is_reviewed(comment: dict[str, Any]) -> bool:
    review = comment.get("_review")
    return isinstance(review, dict) and review.get("reviewed") is True


def has_any_labels(comment: dict[str, Any]) -> bool:
    labels = normalize_labels(comment.get("labels"))
    return any(labels[key] for key in EXPECTED_LABEL_KEYS)


def comment_key(item: ReviewItem, index: int) -> str:
    cid = comment_id(item.comment)
    cref = comment_ref(item.comment, item.fallback_ref)
    return cid or cref or str(index)


def saved_comment_label(item: ReviewItem) -> str:
    cref = comment_ref(item.comment, item.fallback_ref)
    cid = comment_id(item.comment)
    if cid and cref:
        return f"{cref} / {cid}"
    return cref or cid or "current comment"


def editor_widget_prefix(refined_path: Path | str | None, item: ReviewItem, index: int) -> str:
    path_text = str(refined_path or "no-file")
    key_text = comment_key(item, index)
    raw_key = f"{path_text}|{key_text}|{index}"
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_key).strip("_")
    return f"labels_{safe_key}"


def thread_entry_counts(entries: list[ThreadEntry]) -> dict[str, int]:
    return {
        "ready": sum(1 for entry in entries if entry.refined_path is not None),
        "ambiguous": sum(1 for entry in entries if entry.candidates and entry.refined_path is None),
        "missing": sum(1 for entry in entries if entry.missing_refined),
        "total": len(entries),
    }


def load_notice_for(input_path: Path, entries: list[ThreadEntry]) -> tuple[str, str]:
    counts = thread_entry_counts(entries)
    message = f"Loaded {counts['ready']} reviewable refined files"
    detail = (
        f"{counts['ready']} ready | {counts['ambiguous']} ambiguous | "
        f"{counts['missing']} missing | Path: {input_path}"
    )
    return message, detail


def find_refined_files(path: Path) -> list[Path]:
    path = Path(path)
    if path.is_file():
        if path.name != REFINED_JSON_NAME:
            raise ValueError(f"Expected a {REFINED_JSON_NAME} file: {path}")
        return [path]
    if not path.exists():
        raise ValueError(f"Path does not exist: {path}")
    return sorted(candidate for candidate in path.rglob(REFINED_JSON_NAME) if candidate.is_file())


def nearest_thread_folder(refined_path: Path, root_path: Path | None = None) -> Path:
    refined_path = Path(refined_path)
    root_path = Path(root_path).resolve() if root_path is not None and Path(root_path).exists() else None
    for parent in [refined_path.parent, *refined_path.parents]:
        if (parent / SOURCE_JSON_NAME).exists():
            return parent
        if root_path is not None and parent.resolve() == root_path:
            break

    if root_path is not None:
        try:
            relative = refined_path.resolve().relative_to(root_path)
        except ValueError:
            return refined_path.parent
        if relative.parts:
            return root_path / relative.parts[0]
    return refined_path.parent


def thread_title_from_path(thread_folder: Path) -> str:
    structured_path = thread_folder / SOURCE_JSON_NAME
    if structured_path.exists():
        try:
            data = load_json(structured_path)
            title = data.get("post_title") or data.get("title")
            if isinstance(title, str) and title.strip():
                return title.strip()
        except Exception:
            pass
    return thread_folder.name


def discover_thread_entries(input_path: Path) -> list[ThreadEntry]:
    input_path = Path(input_path)
    refined_files = find_refined_files(input_path)
    if input_path.is_file():
        thread_folder = nearest_thread_folder(input_path, input_path.parent)
        return [
            ThreadEntry(
                thread_folder=thread_folder,
                candidates=[input_path],
                refined_path=input_path,
                title=thread_title_from_path(thread_folder),
            )
        ]

    grouped: dict[Path, list[Path]] = {}
    for refined_path in refined_files:
        thread_folder = nearest_thread_folder(refined_path, input_path)
        grouped.setdefault(thread_folder, []).append(refined_path)

    structured_folders = {
        path.parent for path in input_path.rglob(SOURCE_JSON_NAME) if path.is_file()
    }
    for thread_folder in structured_folders:
        grouped.setdefault(thread_folder, [])

    entries: list[ThreadEntry] = []
    for thread_folder in sorted(grouped):
        candidates = sorted(grouped[thread_folder])
        refined_path = candidates[0] if len(candidates) == 1 else None
        entries.append(
            ThreadEntry(
                thread_folder=thread_folder,
                candidates=candidates,
                refined_path=refined_path,
                title=thread_title_from_path(thread_folder),
                missing_refined=not candidates,
            )
        )
    return entries


def display_thread_label(entry: ThreadEntry) -> str:
    status = "missing"
    if entry.refined_path is not None:
        status = "ready"
    elif entry.candidates:
        status = "ambiguous"
    return f"{entry.thread_folder.name} ({status})"


def filtered_items(items: list[ReviewItem], filter_name: str) -> list[tuple[int, ReviewItem]]:
    indexed = list(enumerate(items))
    if filter_name == "Unreviewed":
        return [(index, item) for index, item in indexed if not is_reviewed(item.comment)]
    if filter_name == "Has labels":
        return [(index, item) for index, item in indexed if has_any_labels(item.comment)]
    if filter_name == "Empty labels":
        return [(index, item) for index, item in indexed if not has_any_labels(item.comment)]
    return indexed


def read_current_widget_labels(
    widget_prefix: str,
    existing_labels: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    existing_labels = existing_labels or empty_labels()
    labels = empty_labels()
    for key in EXPECTED_LABEL_KEYS:
        state_key = f"{widget_prefix}_{key}"
        if state_key in st.session_state:
            labels[key] = parse_label_text(str(st.session_state.get(state_key, "")))
        else:
            labels[key] = list(existing_labels.get(key, []))
    return labels


def save_reviewed_comment(
    data: dict[str, Any],
    refined_path: Path,
    item: ReviewItem,
    widget_prefix: str,
) -> None:
    before = copy.deepcopy(item.comment.get("labels"))
    existing_labels = normalize_labels(before)
    after = read_current_widget_labels(widget_prefix, existing_labels)
    item.comment["labels"] = after
    item.comment["_review"] = {
        "reviewed": True,
        "reviewed_at": now_timestamp(),
        "app": APP_NAME,
        "labels_changed": labels_changed(before, after),
    }
    save_json_atomic(data, refined_path)


def load_review_document(refined_path: Path) -> tuple[dict[str, Any], list[ReviewItem]]:
    data = load_json(refined_path)
    if not isinstance(data, dict):
        raise ValueError(f"{refined_path} must contain a JSON object")
    items = iter_review_items(data.get("comments", []))
    return data, items


def short_text(text: Any, max_chars: int = 180) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def format_comment_heading(comment: dict[str, Any], fallback_ref: str = "") -> str:
    cref = comment_ref(comment, fallback_ref)
    cid = comment_id(comment)
    author = comment.get("author") or "unknown"
    level = comment.get("level", "?")
    pieces = [f"{cref}", f"level {level}", f"u/{author}"]
    if cid:
        pieces.append(cid)
    return " | ".join(pieces)


def render_post_context(data: dict[str, Any]) -> None:
    title = data.get("post_title") or data.get("title") or "(untitled post)"
    post_text = data.get("post_text") or ""
    with st.expander("Post context", expanded=False):
        st.markdown(f"**{title}**")
        if post_text:
            st.text_area("Post body", str(post_text), height=120, disabled=True)
        else:
            st.caption("No post body in this JSON.")


def render_reply_chain(item: ReviewItem) -> None:
    with st.expander("Reply chain", expanded=False):
        if not item.ancestors:
            st.caption("This comment replies directly to the post.")
            return
        for ancestor in item.ancestors:
            st.markdown(f"**{format_comment_heading(ancestor)}**")
            st.caption(short_text(ancestor.get("body"), 500))


def render_direct_replies(item: ReviewItem) -> None:
    replies = item.comment.get("replies", [])
    with st.expander(f"Direct replies ({len(replies) if isinstance(replies, list) else 0})", expanded=False):
        if not isinstance(replies, list) or not replies:
            st.caption("No direct replies.")
            return
        for reply in replies:
            if not isinstance(reply, dict):
                continue
            st.markdown(f"**{format_comment_heading(reply)}**")
            st.caption(short_text(reply.get("body"), 500))


def initialize_session_defaults() -> None:
    defaults = {
        "loaded_input_path": "",
        "thread_entries": [],
        "thread_index": 0,
        "candidate_choices": {},
        "data": None,
        "review_items": [],
        "refined_path": None,
        "comment_index": 0,
        "filter_name": "All",
        "status_message": "",
        "notice_kind": "info",
        "notice_message": "",
        "notice_detail": "",
        "notice_timestamp": "",
        "toast_message": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def set_notice(kind: str, message: str, detail: str = "", toast: str | None = None) -> None:
    st.session_state.notice_kind = kind
    st.session_state.notice_message = message
    st.session_state.notice_detail = detail
    st.session_state.notice_timestamp = now_timestamp()
    st.session_state.status_message = message
    if toast is not None:
        st.session_state.toast_message = toast


def render_notice() -> None:
    toast_message = st.session_state.get("toast_message", "")
    if toast_message:
        st.toast(toast_message)
        st.session_state.toast_message = ""

    message = st.session_state.get("notice_message", "")
    if not message:
        return

    detail = st.session_state.get("notice_detail", "")
    timestamp = st.session_state.get("notice_timestamp", "")
    body = message
    if detail:
        body = f"{body}\n\n{detail}"
    if timestamp:
        body = f"{body}\n\n{timestamp}"

    kind = st.session_state.get("notice_kind", "info")
    if kind == "success":
        st.success(body)
    elif kind == "warning":
        st.warning(body)
    elif kind == "error":
        st.error(body)
    else:
        st.info(body)


def reset_loaded_document() -> None:
    st.session_state.data = None
    st.session_state.review_items = []
    st.session_state.refined_path = None
    st.session_state.comment_index = 0


def open_refined_path(refined_path: Path) -> None:
    data, items = load_review_document(refined_path)
    st.session_state.data = data
    st.session_state.review_items = items
    st.session_state.refined_path = refined_path
    st.session_state.comment_index = 0


def load_input_path(input_path_text: str) -> None:
    input_path = Path(input_path_text).expanduser()
    entries = discover_thread_entries(input_path)
    st.session_state.loaded_input_path = str(input_path)
    st.session_state.thread_entries = entries
    st.session_state.thread_index = 0
    st.session_state.candidate_choices = {}
    reset_loaded_document()
    for index, entry in enumerate(entries):
        if entry.refined_path is not None:
            st.session_state.thread_index = index
            open_refined_path(entry.refined_path)
            break
    message, detail = load_notice_for(input_path, entries)
    set_notice("success", message, detail, toast=message)


def save_current_if_loaded() -> bool:
    data = st.session_state.get("data")
    refined_path = st.session_state.get("refined_path")
    items = st.session_state.get("review_items", [])
    comment_index = st.session_state.get("comment_index", 0)
    if data is None or refined_path is None or not items:
        return False
    if comment_index < 0 or comment_index >= len(items):
        return False
    item = items[comment_index]
    refined_path = Path(refined_path)
    widget_prefix = editor_widget_prefix(refined_path, item, comment_index)
    save_reviewed_comment(data, refined_path, item, widget_prefix)
    filename = refined_path.name
    label = saved_comment_label(item)
    message = f"Saved {label}"
    detail = f"File: {filename}"
    set_notice("success", message, detail, toast=f"{message} to {filename}")
    return True


def candidate_choice_key(entry: ThreadEntry) -> str:
    return str(entry.thread_folder)


def render_sidebar() -> None:
    st.sidebar.title("Reviewer")
    path_text = st.sidebar.text_input(
        "Local path",
        value=st.session_state.get("loaded_input_path", ""),
        placeholder=r"outputs\singapore_top_20_20260708_124904",
    )
    if st.sidebar.button("Load path", use_container_width=True):
        try:
            load_input_path(path_text)
            st.rerun()
        except Exception as exc:
            st.sidebar.error(str(exc))

    entries: list[ThreadEntry] = st.session_state.get("thread_entries", [])
    ready_count = sum(1 for entry in entries if entry.refined_path is not None)
    ambiguous_count = sum(1 for entry in entries if entry.candidates and entry.refined_path is None)
    missing_count = sum(1 for entry in entries if entry.missing_refined)
    if entries:
        st.sidebar.caption(
            f"{ready_count} ready | {ambiguous_count} ambiguous | {missing_count} missing"
        )
        labels = [display_thread_label(entry) for entry in entries]
        selected_index = st.sidebar.selectbox(
            "Thread",
            options=list(range(len(entries))),
            index=min(st.session_state.thread_index, len(entries) - 1),
            format_func=lambda index: labels[index],
        )
        if selected_index != st.session_state.thread_index:
            save_current_if_loaded()
            st.session_state.thread_index = selected_index
            reset_loaded_document()
            entry = entries[selected_index]
            if entry.refined_path is not None:
                open_refined_path(entry.refined_path)
            st.rerun()

        current_entry = entries[st.session_state.thread_index]
        if current_entry.candidates and current_entry.refined_path is None:
            st.sidebar.warning("Multiple refined files found for this thread.")
            candidate_labels = [str(path.relative_to(current_entry.thread_folder)) for path in current_entry.candidates]
            candidate_index = st.sidebar.selectbox(
                "Refined file",
                options=list(range(len(current_entry.candidates))),
                format_func=lambda index: candidate_labels[index],
            )
            if st.sidebar.button("Use selected refined file", use_container_width=True):
                selected_path = current_entry.candidates[candidate_index]
                current_entry.refined_path = selected_path
                open_refined_path(selected_path)
                st.rerun()
        elif current_entry.missing_refined:
            st.sidebar.warning("No refined_structured.json found in this thread folder.")

    st.sidebar.divider()
    st.session_state.filter_name = st.sidebar.radio(
        "Filter",
        ["All", "Unreviewed", "Has labels", "Empty labels"],
        index=["All", "Unreviewed", "Has labels", "Empty labels"].index(
            st.session_state.get("filter_name", "All")
        ),
    )


def render_thread_progress(items: list[ReviewItem]) -> None:
    reviewed, total, ratio = review_progress(items)
    st.progress(ratio)
    st.caption(f"Reviewed {reviewed} / {total} comments")


def render_navigation(filtered: list[tuple[int, ReviewItem]]) -> tuple[int, ReviewItem] | None:
    if not filtered:
        st.info("No comments match the current filter.")
        return None

    current_index = st.session_state.comment_index
    filtered_positions = [index for index, _ in filtered]
    if current_index not in filtered_positions:
        st.session_state.comment_index = filtered_positions[0]
        st.rerun()

    current_filtered_index = filtered_positions.index(current_index)
    previous_col, position_col, next_col, save_col = st.columns([1, 2, 1, 1])

    with previous_col:
        if st.button("Previous", use_container_width=True, disabled=current_filtered_index == 0):
            save_current_if_loaded()
            st.session_state.comment_index = filtered_positions[current_filtered_index - 1]
            st.rerun()

    with position_col:
        selected_position = st.selectbox(
            "Comment",
            options=list(range(len(filtered))),
            index=current_filtered_index,
            format_func=lambda pos: f"{pos + 1}/{len(filtered)} - {comment_key(filtered[pos][1], filtered[pos][0])}",
            label_visibility="collapsed",
        )
        if selected_position != current_filtered_index:
            save_current_if_loaded()
            st.session_state.comment_index = filtered_positions[selected_position]
            st.rerun()

    with next_col:
        if st.button("Next", use_container_width=True, disabled=current_filtered_index >= len(filtered) - 1):
            save_current_if_loaded()
            st.session_state.comment_index = filtered_positions[current_filtered_index + 1]
            st.rerun()

    with save_col:
        if st.button("Save", use_container_width=True):
            save_current_if_loaded()
            st.rerun()

    return st.session_state.comment_index, st.session_state.get("review_items", [])[st.session_state.comment_index]


def render_review_item(item: ReviewItem, index: int, data: dict[str, Any]) -> None:
    comment = item.comment
    labels = normalize_labels(comment.get("labels"))
    widget_prefix = editor_widget_prefix(st.session_state.get("refined_path"), item, index)
    st.subheader(format_comment_heading(comment, item.fallback_ref))
    meta_cols = st.columns(4)
    meta_cols[0].metric("Replying to", str(comment.get("replying_to") or "POST"))
    meta_cols[1].metric("Score", str(comment.get("score", "")))
    meta_cols[2].metric("Reviewed", "yes" if is_reviewed(comment) else "no")
    meta_cols[3].metric("Index", str(index + 1))

    body = str(comment.get("body") or "")
    live_labels = read_current_widget_labels(widget_prefix, labels)
    highlighted_html, unmatched = highlighted_comment_html(body, live_labels)
    st.markdown("**Comment body**")
    st.markdown(
        f"<div style='background-color: #262730; border-radius: 0.45rem; padding: 0.85rem; "
        f"min-height: 7rem; white-space: normal; line-height: 1.55;'>{highlighted_html}</div>",
        unsafe_allow_html=True,
    )
    unmatched_text = unmatched_label_text(unmatched)
    if unmatched_text:
        st.caption(f"Not found in comment body: {unmatched_text}")
    render_post_context(data)
    render_reply_chain(item)
    render_direct_replies(item)

    label_cols = st.columns(3)
    for label_col, key in zip(label_cols, EXPECTED_LABEL_KEYS):
        with label_col:
            st.text_area(
                key,
                value=labels_to_text(labels, key),
                height=130,
                key=f"{widget_prefix}_{key}",
            )


def run_app() -> None:
    st.set_page_config(page_title="Reddit Comment Reviewer", layout="wide")
    initialize_session_defaults()
    render_sidebar()

    st.title("Reddit Comment Label Reviewer")
    render_notice()

    data = st.session_state.get("data")
    refined_path = st.session_state.get("refined_path")
    items: list[ReviewItem] = st.session_state.get("review_items", [])
    if data is None or refined_path is None:
        st.info("Paste a local refined_structured.json path, thread folder path, or run folder path to start.")
        return

    st.caption(str(refined_path))
    render_thread_progress(items)
    filtered = filtered_items(items, st.session_state.get("filter_name", "All"))
    selected = render_navigation(filtered)
    if selected is None:
        return
    index, item = selected
    render_review_item(item, index, data)


def _self_test() -> None:
    labels = parse_label_text("Alice, Bob\nAlice\nTampines")
    assert labels == ["Alice", "Bob", "Tampines"]

    progress_items = [
        ReviewItem({"_review": {"reviewed": True}}, "C001", []),
        ReviewItem({}, "C002", []),
    ]
    assert review_progress(progress_items) == (1, 2, 0.5)

    highlight_labels = {
        "names": ["Alice", "Alice Tan"],
        "locations": ["<mall>"],
        "singlish": ["lah", "Lah"],
    }
    highlighted, unmatched = highlighted_comment_html("Alice Tan went to <mall> lah", highlight_labels)
    assert "Alice Tan" in highlighted
    assert "&lt;mall&gt;" in highlighted
    assert "background-color: #ffe58a" in highlighted
    assert "background-color: #9fd8ff" in highlighted
    assert "background-color: #a8f0bf" in highlighted
    assert unmatched["singlish"] == ["Lah"]
    assert highlighted.count("background-color: #ffe58a") == 1

    prefix_item = ReviewItem(
        comment={"comment_ref": "C006", "comment_id": "ow1ztq0"},
        fallback_ref="C006",
        ancestors=[],
    )
    prefix = editor_widget_prefix(Path("folder/refined_structured.json"), prefix_item, 5)
    assert "ow1ztq0" in prefix
    assert " " not in prefix
    assert prefix == editor_widget_prefix(Path("folder/refined_structured.json"), prefix_item, 5)
    assert prefix != editor_widget_prefix(Path("folder/refined_structured.json"), prefix_item, 6)

    ready_entry = ThreadEntry(Path("thread"), [Path("thread/refined_structured.json")], Path("thread/refined_structured.json"), "thread")
    missing_entry = ThreadEntry(Path("missing"), [], None, "missing", missing_refined=True)
    message, detail = load_notice_for(Path("root"), [ready_entry, missing_entry])
    assert message == "Loaded 1 reviewable refined files"
    assert "1 ready" in detail
    assert "1 missing" in detail

    sample = {
        "comments": [
            {
                "body": "top",
                "labels": {"names": ["Alice"], "locations": [], "singlish": []},
                "replies": [{"body": "reply", "replies": []}],
            }
        ]
    }
    items = iter_review_items(sample["comments"])
    assert len(items) == 2
    assert items[0].fallback_ref == "C001"
    assert items[1].fallback_ref == "C001.1"

    temp_root = Path(os.environ.get("STREAMLIT_REVIEWER_TEST_DIR", str(Path.cwd() / "streamlit" / "_self_test_tmp")))
    root = temp_root / f"case_{os.getpid()}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        thread_one = root / "001_thread"
        thread_one.mkdir()
        (thread_one / SOURCE_JSON_NAME).write_text(json.dumps({"title": "Thread one"}), encoding="utf-8")
        annotation_dir = thread_one / "annotation"
        annotation_dir.mkdir()
        refined = annotation_dir / REFINED_JSON_NAME
        refined.write_text(json.dumps(sample), encoding="utf-8")

        thread_two = root / "002_thread"
        thread_two.mkdir()
        (thread_two / SOURCE_JSON_NAME).write_text(json.dumps({"title": "Thread two"}), encoding="utf-8")
        direct_refined = thread_two / REFINED_JSON_NAME
        direct_refined.write_text(json.dumps(sample), encoding="utf-8")

        entries = discover_thread_entries(root)
        ready = [entry for entry in entries if entry.refined_path is not None]
        assert len(ready) == 2
        assert refined in ready[0].candidates or refined in ready[1].candidates
        assert direct_refined in ready[0].candidates or direct_refined in ready[1].candidates

        save_json_atomic(sample, refined)
        assert refined.exists()
        assert backup_path_for(refined).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        if temp_root.exists() and not any(temp_root.iterdir()):
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    if os.environ.get("STREAMLIT_REVIEWER_SELF_TEST") == "1":
        _self_test()
    else:
        run_app()




















