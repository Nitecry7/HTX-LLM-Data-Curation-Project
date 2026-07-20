# Reddit Comment Label Reviewer

## Run

```bash
pip install -r requirements.txt
streamlit run streamlit/streamlit_comment_annotator.py
```

The sidebar contains a local path input. Paste one of these paths:

- a direct path to `refined_structured.json`
- a thread folder that contains `refined_structured.json` somewhere inside it
- a parent run folder that contains many thread folders

## Behaviour

The app is local-path-only because this is the only workflow that can write back in place. It edits `refined_structured.json`, not the source `structured.json`.

For parent folders, the app recursively finds `refined_structured.json` inside each thread folder. Both of these layouts are valid:

```text
thread_folder/
  refined_structured.json
```

```text
thread_folder/
  annotation/
    refined_structured.json
```

The app recursively includes every comment in `comments` and every nested object in `replies`.

It displays the comment reference, comment ID, comment body, the post context, the reply chain, direct replies, and editable `names`, `locations`, and `singlish` labels.

A horizontal progress bar shows reviewed comments for the currently selected thread. The progress count is based on comments with `_review.reviewed = true`.

The comment body highlights live label text from the three label editors. Highlighting is exact and case-sensitive: if a label uses different casing from the comment text, it will not be highlighted. Label categories use separate colors for names, locations, and Singlish. Labels not found in the comment body are shown below the body as a small note.

Clicking Previous, Next, selecting another comment, switching threads, or Save updates the current comment's `labels` object and writes the complete JSON document back to the same file. Saving creates a one-time `.bak` copy beside the refined file, then uses a temporary file followed by replacement to reduce the risk of leaving a partially written JSON file.

Saved comments also get `_review` metadata with reviewed status, timestamp, app name, and whether labels changed.

Successful saves show a brief toast and a persistent success message near the top of the page. Loading a valid file, thread folder, or parent run folder shows a success summary with ready, ambiguous, and missing counts.

Use one label per line. Comma separated labels are also accepted.



