# Ship It Export Guidelines

Use **Ship It** after reviewing one or more `refined_structured.json` files in the Streamlit reviewer. It packages the reviewed refined files into a clean folder that is ready to hand off or upload.

Ship It does not edit `structured.json`, does not change the source thread folders, and does not copy merge logs or other annotation files. It only exports the selected `refined_structured` file format into new thread subfolders.

## How to export

1. Start the reviewer.

   ```powershell
   streamlit run streamlit/streamlit_comment_annotator.py
   ```

2. Load a local path in the sidebar. This can be a parent run folder, one thread folder, or one `refined_structured.json` file.

3. Review and save labels as usual. Ship It saves the currently open comment before exporting.

4. Click **Ship It** in the left Reviewer pane below the filters.

5. Choose whether to export all loaded threads or specific thread folders.

6. Choose the output format:

   - `JSON` keeps the exported file as `refined_structured.json`.
   - `TXT` writes the same file content and keeps only `refined_structured.txt`.
   - `Both` writes both `refined_structured.json` and `refined_structured.txt`.

7. Click **Choose destination and export**, then pick a folder in the native file explorer dialog.

## Thread selection

Ship It lists every loaded thread that has a resolved `refined_structured.json`. Threads with missing refined files cannot be exported.

The dialog shows review progress for each thread. If a selected thread is incomplete, Ship It shows a warning, but it still allows export. This is intentional so reviewers can export partial work when needed.

## Text format behavior

The `.txt` option does not rewrite, reformat, summarize, or otherwise modify the JSON content. It copies the same file bytes/content and changes only the suffix from `.json` to `.txt`.

## Destination behavior

The exported top-level folder uses the loaded source folder name exactly. For example, if the loaded folder is:

```text
outputs\singapore_top_20_20260714_140810
```

and the destination folder is:

```text
C:\Users\Q1\Desktop\reviewed_exports
```

then Ship It creates:

```text
C:\Users\Q1\Desktop\reviewed_exports\singapore_top_20_20260714_140810
```

If that top-level folder already exists in the chosen destination, Ship It stops with a warning. It does not overwrite existing exports and does not auto-rename the folder.

## Output examples

For `TXT` only:

```text
<chosen destination>/
  singapore_top_20_20260714_140810/
    001_prosecution_to_check_if_teen_who_licked_ijooz_straw_will_have_student_pass_cancelled_case/
      annotation/
        refined_structured.txt
```

For `JSON` only:

```text
<chosen destination>/
  singapore_top_20_20260714_140810/
    001_prosecution_to_check_if_teen_who_licked_ijooz_straw_will_have_student_pass_cancelled_case/
      annotation/
        refined_structured.json
```

For both formats:

```text
<chosen destination>/
  singapore_top_20_20260714_140810/
    001_prosecution_to_check_if_teen_who_licked_ijooz_straw_will_have_student_pass_cancelled_case/
      annotation/
        refined_structured.json
        refined_structured.txt
```

Each selected thread gets its own thread folder under the exported top-level folder. The thread folder name is preserved from the source output folder.
