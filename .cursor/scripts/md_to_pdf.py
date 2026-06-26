#!/usr/bin/env python3
"""Convert a markdown file (with mermaid diagrams) to PDF.

Strategy:
  1. Extract ```mermaid blocks before markdown processing.
  2. For each block, hit mermaid.ink to get an SVG (no JS needed at print time).
  3. Render rest with python-markdown.
  4. Inline SVGs in the HTML.
  5. Use Chrome --headless --print-to-pdf (no JS wait needed; pure static).

Usage:
    python md_to_pdf.py <input.md> [output.pdf]
"""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

import markdown
import requests

CHROME = "google-chrome"
MERMAID_INK = "https://mermaid.ink/svg/{encoded}?type=svg"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
@page {{
    margin: 18mm 16mm;
    size: A4;
}}
html {{
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}}
body {{
    font-family: "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei",
                 "Helvetica Neue", Arial, sans-serif;
    line-height: 1.65;
    max-width: 920px;
    margin: 0 auto;
    color: #1f2328;
    font-size: 11pt;
}}
h1 {{
    font-size: 22pt;
    border-bottom: 2px solid #d1d9e0;
    padding-bottom: 0.3em;
    margin-top: 1.6em;
    page-break-before: auto;
    page-break-after: avoid;
}}
h1:first-of-type {{ page-break-before: avoid; }}
h2 {{
    font-size: 17pt;
    border-bottom: 1px solid #d1d9e0;
    padding-bottom: 0.3em;
    margin-top: 1.4em;
    page-break-after: avoid;
}}
h3 {{
    font-size: 13pt;
    margin-top: 1.2em;
    page-break-after: avoid;
}}
h4 {{
    font-size: 11.5pt;
    page-break-after: avoid;
}}
p {{ margin: 0.6em 0; }}
code {{
    background: #f6f8fa;
    border-radius: 4px;
    padding: 0.15em 0.4em;
    font-family: "SF Mono", "Consolas", "Monaco", "Liberation Mono", monospace;
    font-size: 0.9em;
    color: #d63384;
}}
pre {{
    background: #f6f8fa;
    border-radius: 6px;
    padding: 12px 14px;
    overflow-x: auto;
    border: 1px solid #d1d9e0;
    page-break-inside: avoid;
    font-size: 9pt;
    line-height: 1.5;
}}
pre code {{
    background: transparent;
    padding: 0;
    color: #1f2328;
    font-size: inherit;
}}
.mermaid-svg {{
    text-align: center;
    margin: 1em 0;
    page-break-inside: avoid;
    background: #fafbfc;
    border-radius: 6px;
    padding: 14px 8px;
    border: 1px solid #eaeef2;
    overflow: visible;
}}
.mermaid-svg svg {{
    max-width: 100% !important;
    width: auto !important;
    height: auto !important;
    max-height: 600pt !important;
    display: inline-block;
}}
.mermaid-error {{
    color: #b91c1c;
    background: #fef2f2;
    border: 1px solid #fecaca;
    padding: 10px;
    border-radius: 4px;
    font-family: monospace;
    font-size: 9pt;
    white-space: pre-wrap;
}}
table {{
    border-collapse: collapse;
    margin: 1em 0;
    width: 100%;
    page-break-inside: avoid;
    font-size: 10pt;
}}
th, td {{
    border: 1px solid #d1d9e0;
    padding: 6px 12px;
    text-align: left;
    vertical-align: top;
}}
th {{
    background: #f6f8fa;
    font-weight: 600;
}}
tr:nth-child(even) td {{ background: #fafbfc; }}
blockquote {{
    border-left: 4px solid #6b7280;
    margin: 1em 0;
    padding: 0.4em 0 0.4em 1em;
    color: #4b5563;
    background: #f9fafb;
    border-radius: 0 4px 4px 0;
}}
a {{ color: #0969da; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
ul, ol {{ padding-left: 1.5em; }}
li {{ margin: 0.2em 0; }}
hr {{ border: none; border-top: 1px solid #d1d9e0; margin: 2em 0; }}
</style>
</head>
<body>
{content}
</body>
</html>
"""


def encode_for_mermaid_ink(source: str) -> str:
    """mermaid.ink accepts pako (zlib) + base64url encoding under /svg/pako:<...>
    or plain base64url under /svg/<...>. We use plain base64url for simplicity.
    """
    return base64.urlsafe_b64encode(source.encode("utf-8")).decode("ascii").rstrip("=")


def encode_for_mermaid_ink_pako(source: str) -> str:
    """Compressed encoding works for very large diagrams — falls back if plain hits 414."""
    compressed = zlib.compress(source.encode("utf-8"), level=9)
    return "pako:" + base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")


def render_mermaid_to_svg(source: str, max_retries: int = 3) -> str:
    """Try plain base64 first (works for most diagrams), fall back to pako."""
    encoded = encode_for_mermaid_ink(source)
    url = MERMAID_INK.format(encoded=encoded)
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 414 or r.status_code == 400:
                # URI too long / bad — try pako
                encoded = encode_for_mermaid_ink_pako(source)
                url = MERMAID_INK.format(encoded=encoded)
                r = requests.get(url, timeout=30)
            if r.status_code == 200 and r.text.lstrip().startswith("<svg"):
                return r.text
            print(
                f"  mermaid.ink {r.status_code} (attempt {attempt + 1}/{max_retries})",
                file=sys.stderr,
            )
        except requests.RequestException as e:
            print(f"  mermaid.ink network error: {e}", file=sys.stderr)
        time.sleep(1.5 * (attempt + 1))
    return ""


def extract_mermaid(md_text: str) -> tuple[str, list[str]]:
    pattern = re.compile(r"^```mermaid\s*\n(.*?)\n```$", re.MULTILINE | re.DOTALL)
    blocks: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        blocks.append(m.group(1))
        return f"\n\n@@MERMAID_BLOCK_{len(blocks) - 1}@@\n\n"

    return pattern.sub(_replace, md_text), blocks


def reinject_mermaid(html: str, svgs: list[str], sources: list[str]) -> str:
    for i, (svg, src) in enumerate(zip(svgs, sources)):
        token = f"@@MERMAID_BLOCK_{i}@@"
        if svg:
            replacement = f'<div class="mermaid-svg">{svg}</div>'
        else:
            esc = (
                src.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            replacement = (
                f'<div class="mermaid-error">[mermaid render failed]\n{esc}</div>'
            )

        html = re.sub(
            rf"<p>\s*{re.escape(token)}\s*</p>", replacement, html
        )
        html = html.replace(token, replacement)
    return html


def render_html(md_path: Path) -> str:
    src = md_path.read_text(encoding="utf-8")
    md_no_mermaid, blocks = extract_mermaid(src)

    print(f"  Found {len(blocks)} mermaid block(s)")

    svgs: list[str] = []
    for i, block in enumerate(blocks):
        print(f"  [{i + 1}/{len(blocks)}] rendering ...", end=" ", flush=True)
        svg = render_mermaid_to_svg(block)
        if svg:
            print(f"OK ({len(svg)} bytes)")
        else:
            print("FAILED (will show source as fallback)")
        svgs.append(svg)

    body = markdown.markdown(
        md_no_mermaid,
        extensions=[
            "fenced_code",
            "tables",
            "toc",
            "sane_lists",
            "footnotes",
            "attr_list",
        ],
    )
    body = reinject_mermaid(body, svgs, blocks)

    title = md_path.stem
    return HTML_TEMPLATE.format(title=title, content=body)


def md_to_pdf(md_path: Path, pdf_path: Path) -> None:
    html_str = render_html(md_path)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
        f.write(html_str)
        html_path = Path(f.name)

    print(f"  HTML scratch: {html_path}")
    print(f"  Output PDF:   {pdf_path}")

    user_data_dir = tempfile.mkdtemp(prefix="chrome-pdf-")

    cmd = [
        CHROME,
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        f"--user-data-dir={user_data_dir}",
        f"--print-to-pdf={pdf_path}",
        "--no-pdf-header-footer",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=8000",
        f"file://{html_path}",
    ]

    print(f"  Running chrome headless ...")
    start = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    elapsed = time.monotonic() - start

    if result.returncode != 0:
        print(f"chrome stderr:\n{result.stderr}", file=sys.stderr)
        raise RuntimeError(f"chrome exited {result.returncode}")

    print(f"  Done in {elapsed:.1f}s. PDF size: {pdf_path.stat().st_size / 1024:.1f} KB")

    try:
        html_path.unlink()
        import shutil

        shutil.rmtree(user_data_dir, ignore_errors=True)
    except OSError:
        pass


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)

    md_path = Path(sys.argv[1]).expanduser().resolve()
    if not md_path.exists():
        print(f"ERROR: file not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    pdf_path = (
        Path(sys.argv[2]).expanduser().resolve()
        if len(sys.argv) >= 3
        else md_path.with_suffix(".pdf")
    )
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    md_to_pdf(md_path, pdf_path)


if __name__ == "__main__":
    main()
