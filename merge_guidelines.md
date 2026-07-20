# Manual Annotation Merger Guidelines

Use `merge_annotations.py` after you have copied a thread's `structured.json` into an external LLM together with your prompt, then saved the LLM result as `output.json`.

The script creates `annotation/refined_structured.json` as a copy of `structured.json` with labels appended into matching comments. It does not call an LLM and it does not modify the original `structured.json`.

## Required Folder Layout

For folder mode, each thread folder should contain:

```text
thread_folder/
  structured.json
  output.json
```

After a successful merge, the script creates:

```text
thread_folder/
  annotation/
    refined_structured.json
    refined_merge_log.json
    refined_merge_log.txt
```

If `annotation/refined_structured.json` already exists, the script skips that thread and leaves the existing refined file and logs untouched.

## Expected `output.json` Schema

`output.json` must be a JSON array. Each item should look like this:

```json
{
  "comment_key": "comment id or comment ref copied from structured.json",
  "labels": {
    "names": [],
    "locations": [],
    "singlish": []
  }
}
```

Rules:

- `comment_key` should match a comment's `comment_id` first, or `comment_ref` if no ID match exists.
- `labels` must contain exactly `names`, `locations`, and `singlish`.
- Each label category must be a list of strings.
- Empty arrays are valid.
- Leading and trailing spaces are stripped.
- Duplicate labels in the same category are removed while preserving order.

## Commands

Merge one explicit file pair:

```powershell
python merge_annotations.py path\to\structured.json path\to\output.json
```

Use a custom output filename for one file-pair mode:

```powershell
python merge_annotations.py path\to\structured.json path\to\output.json --output refined_structured.json
```

Merge one thread folder:

```powershell
python merge_annotations.py path\to\thread_folder
```

Merge a parent folder containing many thread folders:

```powershell
python merge_annotations.py outputs\some_run_folder
```

In parent-folder mode, the script recursively finds folders that contain both `structured.json` and `output.json`.

## How Matching Works

For every comment and nested reply in `structured.json`, the script tries:

1. Match `output.json` item `comment_key` to the comment's `comment_id`.
2. If no match is found, match `comment_key` to the comment's `comment_ref`.
3. If still no match is found, keep existing labels unchanged or add empty labels if missing.

When a match is found, labels from `output.json` are appended after any existing labels in `structured.json`, then duplicates are removed.

## Common Validation Errors

Fatal errors stop that file from being merged:

- `structured.json` is not valid JSON.
- `output.json` is not valid JSON.
- `output.json` is not a JSON array.

Invalid individual annotation items are skipped and recorded in the log:

- item is not a JSON object
- missing `comment_key`
- missing `labels`
- `labels` has extra or missing keys
- a label category is not a list
- a label value is not a string

Duplicate `comment_key` entries are also logged. The first valid annotation wins and later duplicates are skipped.

## Reading The Merge Log

`annotation/refined_merge_log.json` records the machine-readable merge details for scripts and exact field checks.

`annotation/refined_merge_log.txt` records the same run in a readable summary for quick review.

Both logs are created only when a new `refined_structured.json` is created. If `annotation/refined_structured.json` already exists, the script skips that thread and does not modify either log file.

The logs record:

- source `structured.json` path
- source `output.json` path
- refined output path
- total comments found
- total annotations found
- total comments updated
- comments without annotation
- duplicate annotation keys
- annotation keys that did not match any comment
- skipped invalid annotation items
- validation warnings
- timestamp

Warnings about labels not appearing in the comment body are non-fatal. They are meant to help you review possible LLM mistakes, especially when the model normalized casing or produced a label that was not present in the original text.




