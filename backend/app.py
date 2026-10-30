"""PDF to bitmark: FastAPI backend.

GET  /api/samples   bundled sample PDFs
GET  /api/config    learning-check provider and Web SDK license key
POST /api/convert   {sample: name} or multipart file, quiz_on=1  -> job id
GET  /api/job/{id}  status + result
GET  /api/pdf/{id}  the source PDF (for the viewer)
/jobs/{id}/...      page renders, cropped images, book.bitmark, book.json
"""

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import bitmark_writer
import quiz
import structure

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
OUTPUTS = Path(os.environ.get("OUTPUT_DIR") or ROOT / "outputs")
FRONTEND = ROOT / "frontend"
BOOK_LANGUAGE = os.environ.get("BOOK_LANGUAGE") or "en"
OUTPUTS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="PDF to bitmark")
_jobs = {}
_lock = threading.Lock()


@app.middleware("http")
async def no_cache(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


# ----------------------------------------------------------------- pipeline

def _log(job, message):
    with _lock:
        job["log"].append({"t": round(time.time() - job["started"], 1), "message": message})


def run_job(job):
    work = Path(job["dir"])
    pdf = work / "source.pdf"
    try:
        _log(job, "layout analysis (Nutrient SDK Vision)")
        t = time.time()
        struct = structure.extract(pdf, work)
        _log(job, f"{len(struct['elements'])} elements on {len(struct['pages'])} page(s) in {time.time() - t:.1f}s")

        _log(job, "writing bitmark")
        bits, book = bitmark_writer.build(struct, work, title_hint=job["title"], language=BOOK_LANGUAGE)

        if job["quiz"]:
            _log(job, f"generating learning checks with {quiz.provider()} / {quiz.model_name(quiz.provider())}")
            t = time.time()
            bits = quiz.generate(bits, struct, log=lambda m: _log(job, m))
            _log(job, f"learning checks done in {time.time() - t:.1f}s")

        markup = bitmark_writer.render(bits)
        (work / "book.bitmark").write_text(markup, encoding="utf-8")

        _log(job, "validating with the official bitmark parser")
        proc = subprocess.run(
            ["node", str(Path(__file__).parent / "validate.js"), str(work / "book.bitmark"),
             str(work / "book.json"), str(work / "validation.json")],
            capture_output=True, text=True, timeout=120, cwd=str(Path(__file__).parent))
        if proc.returncode != 0:
            raise RuntimeError("bitmark parser failed: " + proc.stderr[-800:])
        report = json.loads((work / "validation.json").read_text(encoding="utf-8"))

        provenance = {
            "pages": struct["pages"],
            "bits": [b.provenance() for b in bits],
        }
        (work / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=1), encoding="utf-8")

        result = {
            "book": book,
            "markup": markup,
            "bits": [{"id": b.id, "type": b.kind, "markup": b.markup, "page": b.page, "box": b.box, **b.meta} for b in bits],
            "pages": struct["pages"],
            "validation": report,
            "stats": {
                "elements": len(struct["elements"]),
                "pages": len(struct["pages"]),
                "bits": len(bits),
                "seconds": round(time.time() - job["started"], 1),
            },
        }
        (work / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        with _lock:
            job["status"] = "done"
            job["result"] = result
        _log(job, f"done: {len(bits)} bits, parser says {'valid' if report.get('valid') else 'problems'}")
    except Exception as exc:  # surfaced to the UI
        traceback.print_exc()
        with _lock:
            job["status"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"


def _start(pdf_bytes, title, want_quiz):
    digest = hashlib.sha1(pdf_bytes + (b"q" if want_quiz else b"")).hexdigest()[:12]
    work = OUTPUTS / digest
    cached = work / "result.json"
    if cached.exists():
        result = json.loads(cached.read_text(encoding="utf-8"))
        job = {"id": digest, "status": "done", "result": result, "log": [{"t": 0, "message": "loaded from cache"}],
               "started": time.time(), "dir": str(work), "title": title, "quiz": want_quiz}
        with _lock:
            _jobs[digest] = job
        return digest
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / "source.pdf").write_bytes(pdf_bytes)
    job = {"id": digest, "status": "running", "result": None, "log": [], "started": time.time(),
           "dir": str(work), "title": title, "quiz": want_quiz, "error": None}
    with _lock:
        _jobs[digest] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return digest


# ----------------------------------------------------------------- routes

@app.get("/api/samples")
def samples():
    return [{"name": p.name, "size": p.stat().st_size} for p in sorted(SAMPLES.glob("*.pdf"))]


@app.get("/api/config")
def config():
    # An empty Web SDK key runs the viewer in evaluation mode.
    return {"quizProvider": quiz.provider(), "quizModel": quiz.model_name(quiz.provider()),
            "licensed": bool(os.environ.get("NUTRIENT_LICENSE_KEY", "").strip()),
            "webLicenseKey": os.environ.get("NUTRIENT_WEB_LICENSE_KEY", "").strip()}


@app.post("/api/convert")
async def convert(sample: str = Form(None), quiz_on: str = Form("0"), file: UploadFile = File(None)):
    want_quiz = quiz_on in ("1", "true", "on")
    if file is not None:
        data = await file.read()
        title = Path(file.filename or "document.pdf").stem
    elif sample:
        path = SAMPLES / Path(sample).name
        if not path.exists():
            raise HTTPException(404, "sample not found")
        data = path.read_bytes()
        title = path.stem
    else:
        raise HTTPException(400, "send a sample name or a PDF file")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "not a PDF")
    return {"id": _start(data, title, want_quiz)}


@app.get("/api/job/{job_id}")
def job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        work = OUTPUTS / job_id / "result.json"
        if work.exists():
            return {"id": job_id, "status": "done", "log": [], "result": json.loads(work.read_text(encoding="utf-8"))}
        raise HTTPException(404, "unknown job")
    with _lock:
        return {"id": job_id, "status": job["status"], "log": list(job["log"]),
                "result": job["result"], "error": job.get("error")}


@app.get("/api/pdf/{job_id}")
def job_pdf(job_id: str):
    path = OUTPUTS / Path(job_id).name / "source.pdf"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/pdf")


app.mount("/jobs", StaticFiles(directory=str(OUTPUTS)), name="jobs")
app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
