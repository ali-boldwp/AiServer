import os
import json
import uuid
import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, Form, File, UploadFile, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from dotenv import load_dotenv


# -------------------------
# ENV + SETTINGS
# -------------------------
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.environ.get("DATA_DIR", "")).expanduser()
API_KEY = os.environ.get("API_KEY", "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

if not DATA_DIR:
    raise RuntimeError("DATA_DIR is missing in .env")
if not API_KEY:
    raise RuntimeError("API_KEY is missing in .env")

DATA_DIR.mkdir(parents=True, exist_ok=True)


# -------------------------
# HELPERS
# -------------------------
def now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def require_key(x_api_key: str | None):
    # Only for private API endpoints (laptop worker)
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def job_dir(job_id: str) -> Path:
    return DATA_DIR / job_id


def job_json_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def read_job(job_id: str) -> Dict[str, Any]:
    p = job_json_path(job_id)
    if not p.exists():
        raise HTTPException(404, "Job not found")
    return json.loads(p.read_text(encoding="utf-8"))


def write_job(job_id: str, job: Dict[str, Any]):
    # Atomic write to reduce corruption risk
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "job.json.tmp"
    final = d / "job.json"
    tmp.write_text(json.dumps(job, indent=2), encoding="utf-8")
    tmp.replace(final)


async def save_upload(uploads_dir: Path, f: UploadFile | None, name: str) -> Optional[str]:
    if not f or not f.filename:
        return None
    out = uploads_dir / name
    out.write_bytes(await f.read())
    return out.name


def build_download_url(path: str) -> str:
    # For public job pages
    if BASE_URL:
        return f"{BASE_URL}{path}"
    return path


# -------------------------
# PROMPT BUILDER (OPTIONAL)
# -------------------------
STYLE_HINTS = {
    "photoreal": "photorealistic, real human, natural skin texture, realistic eyes, normal iris color",
    "slightly_stylized": "clean semi-realistic, polished, not cartoon, not anime",
    "bold": "bold high-contrast lighting, vibrant colors, crisp edges, thumbnail-ready",
}

EMOTION_HINTS = {
    "surprised": "surprised expression, raised eyebrows, slightly open mouth",
    "excited": "excited expression, energetic smile",
    "serious": "serious expression, confident look",
    "shocked": "shocked expression, dramatic reaction",
}

BASE_NEGATIVE = [
    "watermark", "signature", "jpeg artifacts", "lowres", "blurry", "noise",
    "bad anatomy", "deformed", "disfigured", "extra fingers", "extra limbs",
    "cross-eyed", "lazy eye", "weird pupils", "asymmetrical eyes", "oversized eyes",
    "glowing eyes", "rainbow iris", "doll eyes",
    "macro", "extreme close-up", "eye closeup", "cropped forehead", "cropped chin",
    "cartoon", "anime", "illustration", "painting", "cgi", "3d render",
]

def build_prompts(job: Dict[str, Any]) -> Dict[str, str]:
    video = job.get("video", {})
    creative = job.get("creative", {})

    title = (video.get("title") or "").strip()
    niche = (video.get("niche") or "").strip()
    style = (creative.get("style") or "photoreal").strip().lower()
    emotion = (creative.get("emotion") or "").strip().lower()
    thumb_text = (creative.get("thumb_text") or "").strip()

    style_hint = STYLE_HINTS.get(style, STYLE_HINTS["photoreal"])
    emotion_hint = EMOTION_HINTS.get(emotion, "engaging expression")

    # Default mode: AI face (later you can add background_only mode)
    composition = (
        "YouTube thumbnail, 16:9 composition, medium close-up portrait (head and shoulders), "
        "looking at camera, centered subject, high contrast studio lighting, "
        "clean background, negative space on RIGHT for text, sharp focus"
    )

    positive = ", ".join([
        composition,
        style_hint,
        emotion_hint,
        "one real person, natural proportions, realistic facial features",
        f"topic: {title}" if title else "",
        f"niche: {niche}" if niche else "",
        f"text concept: {thumb_text}" if thumb_text else "",
        "professional, clickable, modern, clean"
    ]).strip(", ").strip()

    # IMPORTANT: keep "text" in negative (AI text looks ugly). You add text in post later.
    negative = ", ".join(dict.fromkeys(BASE_NEGATIVE))

    return {"positive": positive, "negative": negative}


# -------------------------
# APP
# -------------------------
app = FastAPI(title="AI Thumbnail Intake Server")


@app.get("/health", response_class=JSONResponse)
def health():
    return {"ok": True, "time": now_iso()}


@app.get("/favicon.ico")
def favicon():
    # stop 404 spam
    return JSONResponse({"ok": True})


# -----------------------
# PUBLIC: Intake form
# -----------------------
@app.get("/", response_class=HTMLResponse)
def intake_form():
    return """
    <html><body style="font-family:Arial;max-width:820px;margin:25px auto;">
      <h2>Thumbnail Intake Form</h2>
      <form action="/submit" method="post" enctype="multipart/form-data">
        <label>Agent Name</label><br><input name="agent_name" required style="width:100%"><br><br>
        <label>Client Name</label><br><input name="client_name" required style="width:100%"><br><br>
        <label>Client Contact</label><br><input name="client_contact" required style="width:100%"><br><br>

        <label>Video Title</label><br><input name="video_title" required style="width:100%"><br><br>
        <label>Niche</label><br><input name="niche" style="width:100%"><br><br>

        <label>Emotion</label><br><input name="emotion" placeholder="surprised/excited/serious" style="width:100%"><br><br>
        <label>Style</label><br>
        <select name="style">
          <option value="photoreal">photoreal</option>
          <option value="slightly_stylized">slightly_stylized</option>
          <option value="bold">bold</option>
        </select><br><br>

        <label>Thumbnail Text (optional)</label><br><input name="thumb_text" style="width:100%"><br><br>
        <label>Reference links (optional)</label><br><textarea name="ref_links" rows="3" style="width:100%"></textarea><br><br>

        <label>How many options?</label><br>
        <select name="options">
          <option value="3">3</option>
          <option value="5">5</option>
          <option value="10">10</option>
        </select><br><br>

        <label>Face image (optional)</label><br><input type="file" name="face"><br><br>
        <label>Logo (optional)</label><br><input type="file" name="logo"><br><br>
        <label>Reference image (optional)</label><br><input type="file" name="ref_img"><br><br>

        <button type="submit">Submit Job</button>
      </form>
    </body></html>
    """


@app.post("/submit", response_class=HTMLResponse)
async def submit_form(
    agent_name: str = Form(...),
    client_name: str = Form(...),
    client_contact: str = Form(...),
    video_title: str = Form(...),
    niche: str = Form(""),
    emotion: str = Form(""),
    style: str = Form("photoreal"),
    thumb_text: str = Form(""),
    ref_links: str = Form(""),
    options: int = Form(3),
    face: UploadFile | None = File(None),
    logo: UploadFile | None = File(None),
    ref_img: UploadFile | None = File(None),
):
    job_id = "JOB_" + uuid.uuid4().hex[:12].upper()
    d = job_dir(job_id)
    uploads_dir = d / "uploads"
    results_dir = d / "results"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    face_name = await save_upload(uploads_dir, face, "face.png")
    logo_name = await save_upload(uploads_dir, logo, "logo.png")
    ref_name  = await save_upload(uploads_dir, ref_img, "ref.jpg")

    job: Dict[str, Any] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": now_iso(),
        "claimed_at": None,
        "done_at": None,
        "agent": {"name": agent_name},
        "client": {"name": client_name, "contact": client_contact},
        "video": {"title": video_title, "niche": niche},
        "creative": {
            "emotion": emotion,
            "style": style,
            "thumb_text": thumb_text,
            "ref_links": ref_links,
            "options": int(options),
            "size": {"w": 1344, "h": 768},
            # future fields:
            # "subject_mode": "ai_face" or "background_only"
            # "color_theme": ""
            # "must_include": ""
            # "must_avoid": ""
        },
        "uploads": {"face": face_name, "logo": logo_name, "ref_img": ref_name},
        "results": {"zip": None, "preview": None},
    }

    write_job(job_id, job)

    result_page = build_download_url(f"/job/{job_id}")
    return f"""
    <html><body style="font-family:Arial;max-width:820px;margin:25px auto;">
      <h3>✅ Submitted</h3>
      <p><b>Job ID:</b> {job_id}</p>
      <p>Result page:</p>
      <p><a href="{result_page}">{result_page}</a></p>
      <p>(Your laptop worker will pick it up automatically.)</p>
    </body></html>
    """


# -----------------------
# PUBLIC: Result page
# -----------------------
@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(job_id: str):
    job = read_job(job_id)
    z = job["results"]["zip"]
    p = job["results"]["preview"]
    status = job["status"]

    zip_link = f"/download/{job_id}/zip" if z else ""
    prev_link = f"/download/{job_id}/preview" if p else ""

    return f"""
    <html><body style="font-family:Arial;max-width:900px;margin:25px auto;">
      <h2>Job {job_id}</h2>
      <p><b>Status:</b> {status}</p>
      <p><b>Video:</b> {job["video"]["title"]}</p>
      <hr>
      {"<p><a href='"+prev_link+"'>Download Preview</a></p>" if p else "<p>Preview: not ready</p>"}
      {"<p><a href='"+zip_link+"'>Download ZIP</a></p>" if z else "<p>ZIP: not ready</p>"}
      <hr>
      <pre style="background:#f6f6f6;padding:12px;border-radius:8px;">{json.dumps(job, indent=2)}</pre>
    </body></html>
    """


@app.get("/download/{job_id}/zip")
def download_zip(job_id: str):
    job = read_job(job_id)
    zip_name = job["results"]["zip"]
    if not zip_name:
        raise HTTPException(404, "ZIP not ready")
    path = job_dir(job_id) / "results" / zip_name
    return FileResponse(path, filename=zip_name)


@app.get("/download/{job_id}/preview")
def download_preview(job_id: str):
    job = read_job(job_id)
    prev = job["results"]["preview"]
    if not prev:
        raise HTTPException(404, "Preview not ready")
    path = job_dir(job_id) / "results" / prev
    return FileResponse(path, filename=prev)


# -----------------------
# PRIVATE: Laptop worker API
# -----------------------
@app.get("/api/next_job")
def api_next_job(x_api_key: str | None = Header(default=None)):
    require_key(x_api_key)

    # pick oldest queued job
    jobs = sorted([p for p in DATA_DIR.iterdir() if p.is_dir()])

    for d in jobs:
        job_id = d.name
        job = read_job(job_id)

        if job.get("status") != "queued":
            continue

        # claim it
        job["status"] = "processing"
        job["claimed_at"] = now_iso()
        write_job(job_id, job)

        up = job.get("uploads", {})
        def up_url(name):
            return f"/api/job/{job_id}/upload/{name}" if name else None

        prompts = build_prompts(job)  # auto positive/negative

        return {
            "job": job,
            "prompts": prompts,
            "upload_urls": {
                "face": up_url(up.get("face")),
                "logo": up_url(up.get("logo")),
                "ref_img": up_url(up.get("ref_img")),
            }
        }

    return {"job": None}


@app.get("/api/job/{job_id}/upload/{filename}")
def api_get_upload(job_id: str, filename: str, x_api_key: str | None = Header(default=None)):
    require_key(x_api_key)

    path = job_dir(job_id) / "uploads" / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=filename)


@app.post("/api/job/{job_id}/upload_result")
async def api_upload_result(
    job_id: str,
    x_api_key: str | None = Header(default=None),
    zip_file: UploadFile = File(...),
    preview: UploadFile | None = File(None),
):
    require_key(x_api_key)
    job = read_job(job_id)

    results_dir = job_dir(job_id) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    zip_name = f"{job_id}.zip"
    (results_dir / zip_name).write_bytes(await zip_file.read())

    prev_name = None
    if preview and preview.filename:
        prev_name = f"{job_id}_preview.png"
        (results_dir / prev_name).write_bytes(await preview.read())

    job["results"]["zip"] = zip_name
    job["results"]["preview"] = prev_name
    job["status"] = "done"
    job["done_at"] = now_iso()
    write_job(job_id, job)

    return {"ok": True, "job_id": job_id}
