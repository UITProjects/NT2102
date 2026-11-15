---
name: sync-submit
description: Rebuild the course-form submission bundle in the gitignored submit/ folder (report docx + PDF, slides pptx, source zip, demo video, size checks, form-answers.md) from the current latex/, presentation/ and working tree. Use when the user asks to sync / refresh / rebuild / update the submit folder or the submission files, or after edits to latex/ or presentation/ when they say the submission must follow.
argument-hint: "[all|report|slides|source|demo|check ...]"
---

# Sync the submission bundle (`submit/`)

The course form "Nộp đồ án ATBMHTTT" takes: report (docx), slides (pptx), source code, dataset (optional),
demo (optional), each ≤ 100 MB (dataset ≤ 1 GB), one submission per group. `submit/` holds those files plus
`form-answers.md` (text fields to copy and the user's to-do list). It is gitignored (`/submit/` in `.gitignore`).

## Run

From the repo root:

```bash
python3 .claude/skills/sync-submit/scripts/sync_submit.py            # all stages
python3 .claude/skills/sync-submit/scripts/sync_submit.py slides check   # only what changed
```

Pick stages from what changed (pass the user's arguments through if they gave any):

| Changed | Stages |
|---|---|
| `latex/` | `report check` |
| `presentation/` | `slides check` (and `source`, the deck is in the zip) |
| code / docs / anything tracked | `source check` |
| demo video re-recorded | `demo check` (`--demo-src <path>` if not the default `~/[NT2102] Demo anomaly detection on exploit.mp4`) |
| unsure / before the real submit | no argument (all) |

What each stage does:
- `report`: `latexmk -pdf` in `latex/` (fails on undefined refs/cites), then `scripts/build_docx.py`
  (TikZ + algorithm blocks rendered to PNG with the report preamble, paper PDFs to PNG, `\ref`/`\cite` resolved from the
  .aux files, front page with the member table and `[dán ảnh]` / `[SĐT]` placeholders, pandoc), then copies `main.pdf`.
- `slides`: `npx slidev export --format pptx --no-with-clicks` (without `--no-with-clicks` every click becomes a slide: 44 instead of 15).
- `source`: `git ls-files` + untracked-not-ignored, minus `latex/report.docx`; the `OCR-APT/` submodule is left out (the
  form answers link it). Rebuilt from the working tree, so uncommitted edits are included.
- `demo`: copies the video only if it changed.
- `check`: form size limits, pptx slide count (≤ 15), the video-2 placeholder, `\todoval` left; rewrites the size
  column and slide count in `form-answers.md`.

The script exits non-zero on a failed build; show the user the tail of the error, do not paper over it.

## After the run

1. Report the summary table and every WARNING to the user, in plain words.
2. **The docx is regenerated from scratch**: photos and phone numbers the user pasted into the old
   `NT2102-report.docx` are gone. Say so every time the `report` stage ran, before they submit. (Their
   member data never goes into the repo, so the placeholders come back.)
3. If `form-answers.md` text no longer matches the deliverables (slide count, new section, new video link), edit it.
   Keep the field order of the form: Tên nhóm, Tên đề tài, Danh sách thành viên (`;`-separated), Email liên hệ, then
   the five file fields.
4. Spot-check only what changed: a slide image from the pptx (`ppt/media/Slide-N-image-1.png`; LibreOffice Impress is not
   installed here, so the pptx cannot be converted), or the docx via `soffice --headless --convert-to pdf` into the
   scratchpad and `pdftoppm` a few pages. Known cosmetic: LibreOffice shows OMML `<10` as `¿10`.

## Not covered by the script (do only when asked)

- **Dataset** (`NT2102-dataset-sample.zip`): raw ETW of `ws-etw-1`, 2026-11-15 03:30–05:00Z, pulled from Loki on the
  trainer with `loki_pull.py` into `~/NT2102/src/out/etw/experiments/dataset-ws1-react2shell-20261115/` (README with the
  ground truth there). It is a long trainer job: run it as a `systemd-run --slice=user.slice` unit and heartbeat it
  (CLAUDE.md §9). Loki retention will drop that range too, so do not delete the trainer copy while the zip may be needed.
- **UI screenshots** (`submit/shots/`, also `latex/images/res-*.png`, `presentation/images/ws1-subgraph.png`):
  Playwright from `presentation/node_modules/playwright-chromium` on `http://10.21.3.20:8000/#from=...&to=...&host=...`;
  click `#hTable tr[data-w="<window id>"].alert` (the `.alert` copy, not the duplicate row), then screenshot
  `#detail .grid` and `#llm`.
- Redacting `src/ansible/proxmox.ini` in the source zip: only if the user decides to.
- Never upload anything to the form yourself and never commit `submit/`.
