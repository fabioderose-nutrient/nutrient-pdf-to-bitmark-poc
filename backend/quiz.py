"""Optional learning checks per chapter, written as interactive bitmark bits.

The model only sees the text of one chapter at a time and must return JSON
that matches a schema. Each generated quiz quotes a verbatim evidence sentence
which is located back in the chapter elements, so quiz bits get the same
page + bounding box provenance as content bits.

Providers (OpenAI compatible chat endpoints and Anthropic):
  AI_PROVIDER = openai | anthropic | ollama      (auto-detected from keys)
  OPENAI_API_KEY / ANTHROPIC_API_KEY, AI_MODEL, AI_ENDPOINT
"""

import json
import os
import re
import urllib.error
import urllib.request

from bitmark_writer import Bit, breakscape, normalise, property_value, stable_id

SCHEMA = {
    "type": "object",
    "properties": {
        "multiple_choice": {"type": "array", "items": {"type": "object", "properties": {
            "question": {"type": "string"},
            "correct": {"type": "string"},
            "wrong": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "string"},
        }, "required": ["question", "correct", "wrong", "evidence"], "additionalProperties": False}},
        "true_false": {"type": "array", "items": {"type": "object", "properties": {
            "statement": {"type": "string"},
            "is_true": {"type": "boolean"},
            "evidence": {"type": "string"},
        }, "required": ["statement", "is_true", "evidence"], "additionalProperties": False}},
        "flashcards": {"type": "array", "items": {"type": "object", "properties": {
            "term": {"type": "string"},
            "definition": {"type": "string"},
        }, "required": ["term", "definition"], "additionalProperties": False}},
    },
    "required": ["multiple_choice", "true_false", "flashcards"],
    "additionalProperties": False,
}

SYSTEM = ("You are an instructional designer. You write learning checks strictly from the given "
          "chapter text, in the same language as the text. Answer with JSON only.")

PROMPT = ("Chapter title: {title}\n\nChapter text:\n{text}\n\n"
          "Create: {mc} multiple-choice question(s) with exactly one correct answer and 2 plausible wrong "
          "answers; {tf} true/false statement(s) (mix true and false); {fc} flashcard(s) (key term + "
          "one-sentence definition). Every question must be answerable from the chapter text only. "
          "'evidence' must be one sentence copied verbatim from the chapter text.")


def provider():
    p = os.environ.get("AI_PROVIDER", "").lower()
    if p:
        return p
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "ollama"


def model_name(p):
    return os.environ.get("AI_MODEL") or {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-sonnet-5",
        "ollama": "qwen2.5:7b-instruct",
    }.get(p, "gpt-4o-mini")


def _post(url, payload, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ask(title, text, counts):
    p = provider()
    model = model_name(p)
    user = PROMPT.format(title=title, text=text, **counts)
    if p == "anthropic":
        out = _post("https://api.anthropic.com/v1/messages", {
            "model": model, "max_tokens": 2000, "system": SYSTEM,
            "tools": [{"name": "learning_checks", "description": "Return the learning checks",
                       "input_schema": SCHEMA}],
            "tool_choice": {"type": "tool", "name": "learning_checks"},
            "messages": [{"role": "user", "content": user}],
        }, {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"}, 120)
        for block in out.get("content", []):
            if block.get("type") == "tool_use":
                return block["input"]
        raise RuntimeError("no tool_use block in Anthropic response")
    if p == "ollama":
        endpoint = os.environ.get("AI_ENDPOINT") or "http://localhost:11434"
        out = _post(endpoint.rstrip("/") + "/api/chat", {
            "model": model, "stream": False, "format": SCHEMA,
            "options": {"temperature": 0.2, "num_ctx": 8192},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        }, {}, 900)
        return json.loads(out["message"]["content"])
    # OpenAI (or any OpenAI compatible endpoint with AI_ENDPOINT)
    endpoint = os.environ.get("AI_ENDPOINT") or "https://api.openai.com/v1"
    out = _post(endpoint.rstrip("/") + "/chat/completions", {
        "model": model, "temperature": 0.2,
        "response_format": {"type": "json_schema", "json_schema": {"name": "learning_checks", "strict": True, "schema": SCHEMA}},
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    }, {"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"}, 180)
    return json.loads(out["choices"][0]["message"]["content"])


# ----------------------------------------------------------------- grounding

def _locate(evidence, elements):
    """Find the element whose text contains the evidence (normalised, then a
    looser 60-char prefix match). Returns (page, box) or (None, None)."""
    from structure import element_box

    ev = normalise(evidence).lower()
    if not ev:
        return None, None
    candidates = [ev, ev[:60], ev[:40]]
    for cand in candidates:
        if len(cand) < 12:
            continue
        for el in elements:
            text = normalise(el.get("text", "")).lower()
            if cand in text:
                return el.get("pageNumber"), element_box(el)
    return None, None


def chapter_sections(bits, structure):
    """Group content bits under their chapter and collect the chapter text
    (from the layout elements) for the prompt."""
    sections = []
    current = None
    for b in bits:
        if b.kind == "chapter":
            current = {"chapter": b, "bits": [], "text": []}
            sections.append(current)
        elif current is not None and b.kind in ("article", "remark", "table"):
            current["bits"].append(b)
            current["text"].append(_plain(b.markup))
    return sections


def _plain(markup):
    text = re.sub(r"^\[\.[^\]]*\]", "", markup)
    text = re.sub(r"\[@[^\]]*\]", "", text)
    text = re.sub(r"\[#([^\]]*)\]", r"\1", text)
    text = text.replace("===", "\n").replace("==", " | ").replace("**", "").replace("•1 ", "").replace("• ", "- ")
    return normalise(text)


def generate(bits, structure, counts=None, log=None):
    """Return a new bit list with quiz bits appended after each chapter's
    content. Chapters with less than ~200 characters of text are skipped."""
    counts = counts or {"mc": 1, "tf": 1, "fc": 2}
    elements = structure["elements"]
    sections = chapter_sections(bits, structure)
    extra = {}
    for sec in sections:
        text = "\n".join(sec["text"])
        if len(text) < 200:
            continue
        title = sec["chapter"].meta.get("title", "")
        if log:
            log(f"learning checks for: {title}")
        try:
            data = ask(title, text, counts)
        except (urllib.error.URLError, RuntimeError, KeyError, json.JSONDecodeError, TimeoutError) as exc:
            if log:
                log(f"quiz generation failed for '{title}': {exc}")
            continue
        made = []
        dropped = 0
        for q in data.get("multiple_choice", [])[: counts["mc"]]:
            correct = normalise(q.get("correct", ""))
            seen = {correct.lower()}
            wrong = []
            for w in q.get("wrong", []):
                w = normalise(w)
                if w and w.lower() not in seen:
                    wrong.append(w)
                    seen.add(w.lower())
            page, box = _locate(q.get("evidence", ""), elements)
            # keep only grounded questions: the evidence sentence must exist
            if not q.get("question") or not correct or len(wrong) < 2 or not box:
                dropped += 1
                continue
            qid = stable_id("q", q["question"], correct)
            choices = [f"[+{property_value(correct)}]"] + [f"[-{property_value(w)}]" for w in wrong[:2]]
            markup = f"[.multiple-choice-1][@id:{qid}]\n[!{property_value(q['question'])}]\n" + "\n".join(choices)
            made.append(Bit("multiple-choice-1", qid, markup, page, box,
                            {"evidence": q.get("evidence", ""), "generated": True}))
        for s in data.get("true_false", [])[: counts["tf"]]:
            page, box = _locate(s.get("evidence", ""), elements)
            if not s.get("statement") or not box:
                dropped += 1
                continue
            sid = stable_id("tf", s["statement"])
            sign = "+" if s.get("is_true") else "-"
            markup = f"[.true-false-1][@id:{sid}]\n[{sign}{property_value(s['statement'])}]"
            made.append(Bit("true-false-1", sid, markup, page, box,
                            {"evidence": s.get("evidence", ""), "generated": True}))
        if dropped and log:
            log(f"  dropped {dropped} ungrounded or malformed question(s)")
        for f in data.get("flashcards", [])[: counts["fc"]]:
            if not f.get("term") or not f.get("definition"):
                continue
            fid = stable_id("f", f["term"])
            markup = (f"[.flashcard-1][@id:{fid}]\n====\n{breakscape(normalise(f['term']))}\n--\n"
                      f"{breakscape(normalise(f['definition']))}\n====")
            page, box = _locate(f["term"], elements)
            made.append(Bit("flashcard-1", fid, markup, page or sec["chapter"].page, box or sec["chapter"].box,
                            {"generated": True}))
        if made:
            anchor = sec["bits"][-1] if sec["bits"] else sec["chapter"]
            extra[id(anchor)] = made

    out = []
    for b in bits:
        out.append(b)
        out.extend(extra.get(id(b), []))
    return out
