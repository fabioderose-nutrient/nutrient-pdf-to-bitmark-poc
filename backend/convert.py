"""Command line conversion: python3 convert.py input.pdf out_dir [--quiz]"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import bitmark_writer
import quiz
import structure


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    pdf = Path(sys.argv[1])
    out = Path(sys.argv[2])
    want_quiz = "--quiz" in sys.argv
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(pdf, out / "source.pdf")

    t = time.time()
    struct = structure.extract(out / "source.pdf", out)
    print(f"layout: {len(struct['elements'])} elements, {len(struct['pages'])} pages, {time.time() - t:.1f}s")

    bits, book = bitmark_writer.build(struct, out, title_hint=pdf.stem, language=os.environ.get("BOOK_LANGUAGE") or "en")
    if want_quiz:
        print(f"learning checks with {quiz.provider()} / {quiz.model_name(quiz.provider())}")
        bits = quiz.generate(bits, struct, log=print)

    markup = bitmark_writer.render(bits)
    (out / "book.bitmark").write_text(markup, encoding="utf-8")
    (out / "provenance.json").write_text(json.dumps({"pages": struct["pages"], "bits": [b.provenance() for b in bits]},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
    proc = subprocess.run(["node", str(Path(__file__).parent / "validate.js"), str(out / "book.bitmark"),
                           str(out / "book.json"), str(out / "validation.json")],
                          capture_output=True, text=True, cwd=str(Path(__file__).parent))
    print(proc.stdout.strip() or proc.stderr.strip())
    print(f"wrote {out / 'book.bitmark'} ({len(bits)} bits) in {time.time() - t:.1f}s")


if __name__ == "__main__":
    main()
