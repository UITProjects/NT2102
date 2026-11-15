#!/usr/bin/env python3
"""Build the course-form report (.docx) from latex/ with pandoc.

pandoc cannot draw TikZ or algorithmic blocks, and Word cannot resolve \\ref / \\cite. So this script:
  1. renders every tikzpicture and algorithmic block to PNG (standalone pdflatex + pdftoppm), reusing the
     report preamble so colours/styles match the PDF;
  2. renders the paper's PDF figures to PNG;
  3. replaces \\ref{x} with the number LaTeX gave it (latex/contents.aux), \\cite{a,b} with [n, m] (main.aux),
     and appends the bibliography (main.bbl) as a numbered list;
  4. maps the report's custom macros to plain text, puts a front page with a member table (photo / phone
     placeholders) first, and runs pandoc.
Run after `latexmk -pdf main.tex` (the .aux/.bbl files must be fresh). Normally called by sync_submit.py (skill
`sync-submit`); standalone:  python3 .claude/skills/sync-submit/scripts/build_docx.py
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]          # repo root (.claude/skills/sync-submit/scripts/ -> root)
LATEX = ROOT / "latex"
WORK = ROOT / "submit" / "build" / "work"           # gitignored scratch, removed after a successful build
OUT = ROOT / "submit" / "NT2102-report.docx"


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(map(str, cmd))}\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")


def preamble() -> str:
    """main.tex up to \\begin{document}, with the article class swapped for standalone."""
    pre = (LATEX / "main.tex").read_text().split("\\begin{document}")[0]
    pre = pre.replace("\\documentclass[a4paper]{article}", "\\documentclass[border=6pt,varwidth=17cm]{standalone}")
    pre = re.sub(r"\\usepackage\[a4paper[^\]]*\]\{geometry\}", "", pre)   # geometry fights standalone
    pre = pre.replace("\\usepackage[colorinlistoftodos]{todonotes}", "")
    return pre


def render_block(body: str, name: str, pre: str) -> str:
    """Compile one LaTeX block to WORK/<name>.png; return the PNG file name."""
    tex = WORK / f"{name}.tex"
    tex.write_text(pre + "\n\\begin{document}\n" + body + "\n\\end{document}\n")
    run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex.name], WORK)
    run(["pdftoppm", "-png", "-r", "220", "-singlefile", f"{name}.pdf", name], WORK)
    return f"{name}.png"


def labels() -> dict:
    """label -> number, from LaTeX's own aux files (so the docx shows the same numbers as the PDF)."""
    out = {}
    for aux in (LATEX / "contents.aux", LATEX / "main.aux"):
        for m in re.finditer(r"\\newlabel\{([^}]+)\}\{\{([^}]*)\}", aux.read_text()):
            out[m.group(1)] = m.group(2)
    return out


def bibnums() -> dict:
    return {m.group(1): m.group(2) for m in re.finditer(r"\\bibcite\{([^}]+)\}\{\{(\d+)\}", (LATEX / "main.aux").read_text())}


def bibliography(nums: dict) -> str:
    bbl = (LATEX / "main.bbl").read_text()
    items = re.split(r"\\bibitem(?:\[(?:[^\[\]]|\[[^\]]*\])*\])?\{([^}]+)\}", bbl)[1:]
    entries = []
    for key, text in zip(items[0::2], items[1::2]):
        text = text.split("\\end{thebibliography}")[0]
        text = text.replace("\\newblock", " ").replace("\\natexlab", "")
        text = re.sub(r"\\doi\{([^}]*)\}", r"doi: \1", text)
        text = re.sub(r"\s+", " ", text).strip()
        entries.append((int(nums.get(key, 999)), text))
    entries.sort()
    return "\\section*{Tài liệu tham khảo}\n" + "\n\n".join(f"[{n}] {t}" for n, t in entries) + "\n"



def take_braced(s: str, i: int) -> int:
    """s[i] == '{'; return the index just past its matching '}'."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    raise ValueError("unbalanced braces")


def legends_after_figures(s: str) -> str:
    """Move each \\figlegend{...} found inside a figure to just after \\end{figure}: pandoc otherwise lays the
    legend out beside the image and shrinks it."""
    out, pos = [], 0
    while True:
        b = s.find("\\begin{figure}", pos)
        if b < 0:
            out.append(s[pos:])
            return "".join(out)
        e = s.find("\\end{figure}", b) + len("\\end{figure}")
        fig, legends = s[b:e], []
        while (k := fig.find("\\figlegend{")) >= 0:
            end = take_braced(fig, k + len("\\figlegend"))
            legends.append(fig[k + len("\\figlegend") + 1:end - 1])
            fig = fig[:k] + fig[end:]
        out.append(s[pos:b] + fig + "".join("\n\n{\\small " + t + "}\n\n" for t in legends))
        pos = e


def docx_table_spec(spec: str) -> str:
    """tabularx column spec -> plain p{} widths pandoc understands (X columns share 60 %, the rest 40 %)."""
    spec = spec.replace(">{\\raggedright\\arraybackslash}", "")
    cols = re.findall(r"p\{[^}]*\}|[lrcX]", spec)
    nx = sum(c == "X" for c in cols) or 1
    nl = sum(c in "lrc" for c in cols) or 1
    fixed = [c for c in cols if c.startswith("p{")]
    xw, lw = (0.62 if fixed else 0.6) / nx, 0.38 / nl
    return "".join(f"p{{{xw:.2f}\\linewidth}}" if c == "X" else f"p{{{lw:.2f}\\linewidth}}" if c in "lrc" else c
                   for c in cols)

FRONT = r"""
\begin{center}
ĐẠI HỌC QUỐC GIA THÀNH PHỐ HỒ CHÍ MINH\\
TRƯỜNG ĐẠI HỌC CÔNG NGHỆ THÔNG TIN

\includegraphics[width=4cm]{Logo_UIT_updated.jpg}

Môn học: \textbf{An toàn bảo mật hệ thống thông tin}\\
Giảng viên: \textbf{Nguyễn Tấn Cầm}\\
Học kì: 2026--2027

\textbf{BÁO CÁO ĐỒ ÁN}\\
\textbf{Phát hiện bất thường trên endpoint device log dựa trên OCR-APT}
\end{center}

\section*{Danh sách thành viên}

\begin{tabular}{|l|l|l|l|}
\hline
Ảnh chân dung & Họ và tên & Email & Số điện thoại \\
\hline
[dán ảnh] & Ngô Vũ Minh Đạt & 26848033@grad.uit.edu.vn & [SĐT] \\
\hline
[dán ảnh] & Nguyễn Long Vũ & 250202060@grad.uit.edu.vn & [SĐT] \\
\hline
[dán ảnh] & Trần Quang Kiệt & 26848108@grad.uit.edu.vn & [SĐT] \\
\hline
\end{tabular}

Mã nguồn: \url{https://github.com/UITProjects/NT2102} · Video demo: \url{https://www.youtube.com/watch?v=Jzv03v82bNo}

"""

# custom macros of main.tex, as plain text for pandoc
MACROS = r"""
\newcommand{\tagpaper}{[PAPER]}
\newcommand{\tagrepo}{[AUTHOR CODE]}
\newcommand{\tagours}{[OURS]}
\newcommand{\taginf}{[INFERENCE]}
\newcommand{\stepnum}[1]{(#1)}
\newcommand{\swatch}[2]{■ #2}
\newcommand{\arrowkey}[2]{→ #2}
\newcommand{\figlegend}[1]{#1}
"""


def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    OUT.parent.mkdir(exist_ok=True)
    for f in (LATEX / "images").iterdir():
        shutil.copy(f, WORK / f.name)
    shutil.copytree(LATEX / "images", WORK / "images")      # standalone blocks keep their images/ paths
    pre = preamble()
    s = (LATEX / "contents.tex").read_text()
    # pandoc lifts an abstract env into metadata and prints it before everything; keep it in place as a section
    s = s.replace("\\begin{abstract}", "\\section*{Tóm tắt}").replace("\\end{abstract}", "")

    # 1. TikZ pictures and algorithmic blocks -> PNG
    n = 0
    def tikz(m):
        nonlocal n
        n += 1
        png = render_block(m.group(0), f"tikz{n:02d}", pre)
        return f"\\includegraphics[width=\\textwidth]{{{png}}}"
    s = re.sub(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", tikz, s, flags=re.S)
    a = 0
    def algo(m):
        nonlocal a
        a += 1
        png = render_block("\\begin{minipage}{16cm}" + m.group(0) + "\\end{minipage}", f"alg{a:02d}", pre)
        return f"\\includegraphics[width=\\textwidth]{{{png}}}"
    s = re.sub(r"\\begin\{algorithmic\}.*?\\end\{algorithmic\}", algo, s, flags=re.S)
    s = s.replace("\\begin{algorithm}", "\\begin{figure}").replace("\\end{algorithm}", "\\end{figure}")
    # inline \tikz legend symbols -> a plain bullet; legends out of the figure; tabularx -> tabular with widths
    s = re.sub(r"\\tikz(\[[^\]]*\])?\\(node|draw)[^;]*;", "●", s)
    s = legends_after_figures(s)
    out, pos = [], 0
    while (k := s.find("\\begin{tabularx}{\\textwidth}", pos)) >= 0:
        i = k + len("\\begin{tabularx}{\\textwidth}")
        j = take_braced(s, i)
        out.append(s[pos:k] + "\\begin{tabular}{" + docx_table_spec(s[i + 1:j - 1]) + "}")
        pos = j
    s = "".join(out) + s[pos:]
    s = s.replace("\\end{tabularx}", "\\end{tabular}")
    print(f"rendered {n} tikz + {a} algorithm blocks")

    # 2. paper figures (PDF) -> PNG
    for pdf in WORK.glob("paper-fig*.pdf"):
        run(["pdftoppm", "-png", "-r", "200", "-singlefile", pdf.name, pdf.stem], WORK)
    s = re.sub(r"images/(paper-fig[^}]+)\.pdf", r"\1.png", s)
    s = s.replace("images/", "")

    # 3. cross-references and citations -> literal numbers
    lab, cit = labels(), bibnums()
    s = re.sub(r"\\(?:eq)?ref\{([^}]+)\}", lambda m: lab.get(m.group(1), "??"), s)
    s = re.sub(r"\\cite[pt]?\{([^}]+)\}",
               lambda m: "[" + ", ".join(cit.get(k.strip(), "?") for k in m.group(1).split(",")) + "]", s)
    missing = sorted(set(re.findall(r"\?\?", s)))
    if missing:
        print("WARNING: unresolved refs remain")
    s = re.sub(r"\\label\{[^}]+\}", "", s)

    doc = ("\\documentclass{article}\n" + MACROS + "\\begin{document}\n" + FRONT + "\\newpage\n" + s +
           "\n" + bibliography(cit) + "\\end{document}\n")
    (WORK / "docx-main.tex").write_text(doc)

    # 4. pandoc
    run(["pandoc", "docx-main.tex", "-f", "latex", "-o", str(OUT), "--resource-path", str(WORK),
         "-M", "lang=vi"], WORK)
    print("wrote", OUT, f"{OUT.stat().st_size / 1e6:.1f} MB")
    shutil.rmtree(WORK.parent)


if __name__ == "__main__":
    main()
