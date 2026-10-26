"""Turn the Zambian Highway Code PDF into the search index the assistant uses.

    python -m scripts.ingest_highway_code path/to/Zambian-Highway-Code.pdf
    python -m scripts.ingest_highway_code code.pdf --out data/highway_code.jsonl

The official PDF is published by RTSA / the Ministry of Transport and
Communications (https://www.rtsa.org.zm/wp-content/uploads/2019/09/Zambian-Highway-Code.pdf).
Download it yourself and point this script at it. Check the printed report: rule
numbers should run 1..N without gaps. Gaps mean the PDF text did not parse
cleanly there, so fix the source text or the parser before relying on answers.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.highway_code import parse_rules  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", default="data/highway_code.jsonl")
    ap.add_argument("--skip-pages", type=int, default=0, help="front pages to skip (cover, contents)")
    args = ap.parse_args()

    try:
        from pypdf import PdfReader
    except ImportError:
        print("pypdf is not installed: pip install pypdf")
        return 1

    reader = PdfReader(args.pdf)
    text = "\n".join((p.extract_text() or "") for p in reader.pages[args.skip_pages:])
    rules = parse_rules(text)
    if not rules:
        print("No rules found. Try --skip-pages, or check the PDF has selectable text (not scanned images).")
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rules:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    numbers = [r["rule"] for r in rules]
    missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
    print(f"{len(rules)} rules written to {out} (rule {min(numbers)} to {max(numbers)})")
    print(f"Sections found: {len({r['section'] for r in rules if r['section']})}")
    print(f"Missing rule numbers: {missing if missing else 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
