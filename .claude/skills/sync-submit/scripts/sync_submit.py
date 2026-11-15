#!/usr/bin/env python3
"""Rebuild the course-form submission bundle in the gitignored submit/ folder from the current working tree.

Stages (default: all, in this order):
  report  latexmk the report, fail on undefined refs, build NT2102-report.docx (build_docx.py), copy the PDF
  slides  Slidev -> NT2102-slides.pptx (one image per slide, final click state, speaker notes)
  source  NT2102-source.zip: git-tracked + untracked-not-ignored files of the working tree, submodules left out
  demo    copy the demo video to NT2102-demo.mp4 when the source file changed
  check   size limits of the form, slide count, open placeholders; rewrites the size column of form-answers.md

  python3 .claude/skills/sync-submit/scripts/sync_submit.py            # everything
  python3 .claude/skills/sync-submit/scripts/sync_submit.py slides check

The dataset zip and the UI screenshots are NOT rebuilt here: they come from the trainer / live UI (see SKILL.md).
"""
import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SUBMIT = ROOT / "submit"
HERE = Path(__file__).resolve().parent
DEFAULT_DEMO = Path.home() / "[NT2102] Demo anomaly detection on exploit.mp4"
SOURCE_EXCLUDE = {"latex/report.docx"}        # an old hand-made docx, not part of the code

MB = 1e6
LIMITS = {                                     # form field limits
    "NT2102-report.docx": 100 * MB,
    "NT2102-slides.pptx": 100 * MB,
    "NT2102-source.zip": 100 * MB,
    "NT2102-demo.mp4": 100 * MB,
    "NT2102-dataset-sample.zip": 1000 * MB,
}
warnings: list[str] = []


def run(cmd, cwd, timeout=900):
    print("$", " ".join(map(str, cmd)), f"  (in {cwd.relative_to(ROOT) if cwd != ROOT else '.'})")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=timeout)
    if r.returncode != 0:
        sys.exit(f"FAILED ({r.returncode}): {' '.join(map(str, cmd))}\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r.stdout


def report():
    latex = ROOT / "latex"
    run(["latexmk", "-pdf", "-interaction=nonstopmode", "main.tex"], latex)
    log = (latex / "main.log").read_text(errors="replace")
    if re.search(r"undefined references|Reference `[^']+' on page \d+ undefined|Citation `[^']+' .*undefined", log):
        sys.exit("FAILED: latex/main.log reports undefined references or citations")
    pages = re.search(r"Output written on main\.pdf \((\d+) pages", log)
    print("  report PDF:", pages.group(1) if pages else "?", "pages")
    todo = len(re.findall(r"\\todoval\{", (latex / "contents.tex").read_text()))
    if todo:
        warnings.append(f"latex/contents.tex still has {todo} \\todoval placeholder(s)")
    print(run([sys.executable, str(HERE / "build_docx.py")], ROOT).strip())
    shutil.copy2(latex / "main.pdf", SUBMIT / "NT2102-report.pdf")


def slides():
    pres = ROOT / "presentation"
    out = SUBMIT / "NT2102-slides.pptx"
    run(["npx", "slidev", "export", "--format", "pptx", "--no-with-clicks", "--output", str(out)], pres, timeout=900)
    print("  slides:", slide_count(out))


def slide_count(pptx: Path) -> int:
    with zipfile.ZipFile(pptx) as z:
        return sum(bool(re.fullmatch(r"ppt/slides/slide\d+\.xml", n)) for n in z.namelist())


def source():
    def ls(*extra):
        out = subprocess.run(["git", "ls-files", "-z", *extra], cwd=ROOT, capture_output=True, check=True).stdout
        return set(out.decode().split("\0")) - {""}
    files = sorted((ls() | ls("--others", "--exclude-standard")) - SOURCE_EXCLUDE)
    out, n, dirs = SUBMIT / "NT2102-source.zip", 0, []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            p = ROOT / f
            if p.is_file():
                z.write(p, "NT2102/" + f)
                n += 1
            elif p.is_dir():                    # a submodule (OCR-APT/)
                dirs.append(f)
    print(f"  source: {n} files, left out (submodules): {dirs}")
    if (ROOT / "src/ansible/proxmox.ini").is_file():
        warnings.append("source zip includes src/ansible/proxmox.ini (lab WinRM / Grafana / Kali passwords)")


def demo(src: Path):
    dst = SUBMIT / "NT2102-demo.mp4"
    if not src.is_file():
        warnings.append(f"demo video not found: {src} (kept the existing copy)" if dst.exists()
                        else f"demo video not found: {src}")
        return
    if dst.exists() and dst.stat().st_size == src.stat().st_size and dst.stat().st_mtime >= src.stat().st_mtime:
        print("  demo: unchanged")
        return
    shutil.copy2(src, dst)
    print("  demo: copied", src.name)


def check():
    rows = []
    for name, limit in LIMITS.items():
        p = SUBMIT / name
        if not p.exists():
            rows.append((name, "MISSING", ""))
            warnings.append(f"{name} is missing")
            continue
        size = p.stat().st_size
        rows.append((name, f"{size / MB:.1f} MB", "OK" if size <= limit else f"OVER {limit / MB:.0f} MB"))
        if size > limit:
            warnings.append(f"{name} is over the form limit")
    pptx = SUBMIT / "NT2102-slides.pptx"
    n_slides = slide_count(pptx) if pptx.exists() else None
    if n_slides and n_slides > 20:
        warnings.append(f"pptx has {n_slides} slides (deck rule: max 20; was it exported with clicks?)")
    if "<VIDEO_ID>" in (ROOT / "presentation/slides.md").read_text():
        warnings.append("slide '3 · Video demo 2' is still a placeholder (<VIDEO_ID> in presentation/slides.md)")
    update_form_answers({n: s for n, s, _ in rows if s != "MISSING"}, n_slides)
    print("\n  file                        size      limit")
    for name, size, status in rows:
        print(f"  {name:<27} {size:<9} {status}")


def update_form_answers(sizes: dict, n_slides):
    """Rewrite the size cell (first 'x.y MB') of each file row in form-answers.md, and the slide count."""
    p = SUBMIT / "form-answers.md"
    if not p.exists():
        warnings.append("submit/form-answers.md is missing (recreate it from the form fields in SKILL.md)")
        return
    lines = p.read_text().split("\n")
    for i, line in enumerate(lines):
        for name, size in sizes.items():
            if line.startswith("|") and f"`{name}`" in line:
                cells = line.split("|")
                cells[-2] = re.sub(r"[\d.]+ MB", size, cells[-2], count=1)
                line = "|".join(cells)
                if n_slides and name.endswith(".pptx"):
                    line = re.sub(r"\(\d+ slide\)", f"({n_slides} slide)", line)
                lines[i] = line
    p.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stages", nargs="*", choices=["all", "report", "slides", "source", "demo", "check"])
    ap.add_argument("--demo-src", type=Path, default=DEFAULT_DEMO, help="demo video to copy (default: %(default)s)")
    a = ap.parse_args()
    stages = ["report", "slides", "source", "demo", "check"] if not a.stages or "all" in a.stages else a.stages
    SUBMIT.mkdir(exist_ok=True)
    if subprocess.run(["git", "check-ignore", "-q", "submit/x"], cwd=ROOT).returncode != 0:
        sys.exit("FAILED: submit/ is not gitignored; add '/submit/' to .gitignore first")
    for st in stages:
        print(f"== {st}")
        demo(a.demo_src) if st == "demo" else globals()[st]()
    if warnings:
        print("\nWARNINGS:")
        print("\n".join(f"  - {w}" for w in warnings))


if __name__ == "__main__":
    main()
