import os, json, uuid, datetime
from pathlib import Path
from typing import Any
from fastapi import FastAPI, Form, File, UploadFile, Header, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
API_KEY = os.environ.get("API_KEY", "").strip()
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "0").strip() == "1" or (API_KEY == "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
CONFIG_PATH = Path(os.environ.get("THUMBOS_CONFIG", "/thumbos_config.json"))

DATA_DIR.mkdir(parents=True, exist_ok=True)

def require_key(x_api_key: str | None):
    if AUTH_DISABLED:
        return
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"

def job_dir(job_id: str) -> Path:
    return DATA_DIR / job_id

def read_job(job_id: str) -> dict:
    p = job_dir(job_id) / "job.json"
    if not p.exists():
        raise HTTPException(404, "Job not found")
    return json.loads(p.read_text(encoding="utf-8"))

def write_job(job_id: str, job: dict):
    p = job_dir(job_id) / "job.json"
    p.write_text(json.dumps(job, indent=2), encoding="utf-8")

def load_cfg() -> dict:
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"thumbos_config.json not found at: {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

def norm(s: str) -> str:
    return (s or "").strip().lower()

def auto_pick_niche(cfg: dict, title: str, niche_hint: str) -> str:
    nh = norm(niche_hint)
    if nh in cfg["niches"]:
        return nh
    text = (norm(title) + " " + nh).strip()
    best, best_score = "ai_tech", 0
    for k, v in cfg["niches"].items():
        score = sum(1 for kw in v.get("keywords", []) if kw in text)
        if score > best_score:
            best, best_score = k, score
    return best

def auto_pick_archetype(cfg: dict, has_face: bool) -> str:
    return "face_big_text" if has_face else "no_face_big_text"

def build_strategy(cfg: dict, job: dict) -> dict:
    title = job.get("video", {}).get("title", "")
    niche_hint = job.get("video", {}).get("niche", "")
    has_face = bool(job.get("uploads", {}).get("face"))

    archetype = norm(job.get("creative", {}).get("archetype", "AUTO"))
    style = norm(job.get("creative", {}).get("style", "photoreal"))
    emotion = norm(job.get("creative", {}).get("emotion", "AUTO"))
    color = norm(job.get("creative", {}).get("color_theme", "AUTO"))

    niche_key = auto_pick_niche(cfg, title, niche_hint)
    if archetype in ["auto", ""]:
        archetype = auto_pick_archetype(cfg, has_face)

    arch = cfg["archetypes"].get(archetype) or cfg["archetypes"]["face_big_text"]
    layout = arch["layout"]
    prompt_mode = arch["prompt_mode"]

    if color in ["auto", ""]:
        color = cfg["niches"][niche_key]["color_themes"][0]

    return {
        "niche_key": niche_key,
        "archetype": archetype,
        "layout": layout,
        "prompt_mode": prompt_mode,
        "style": style if style in cfg["styles"] else "photoreal",
        "emotion": emotion if emotion in cfg["emotions"] else "AUTO",
        "color_theme": color if color in cfg["color_themes"] else cfg["niches"][niche_key]["color_themes"][0],
        "has_face": has_face
    }

BASE_NEGATIVE = (
    "watermark, signature, text, letters, typography, logo, lowres, blurry, noise, jpeg artifacts, "
    "bad anatomy, deformed, disfigured, extra limbs, extra fingers, cross-eyed, weird pupils, "
    "cartoon, anime, illustration, painting, cgi, 3d render"
)

def build_prompts(cfg: dict, job: dict, strategy: dict) -> dict:
    title = job.get("video", {}).get("title", "")
    niche = cfg["niches"][strategy["niche_key"]]
    cues = ", ".join(niche.get("visual_cues", []))

    style_p = cfg["styles"][strategy["style"]]["prompt"]
    emo_p = cfg["emotions"][strategy["emotion"]]["prompt"]
    col_p = cfg["color_themes"][strategy["color_theme"]]["prompt"]

    composition = (
        "YouTube thumbnail background, 16:9, high contrast, vibrant but clean, sharp focus, "
        "BIG CLEAN NEGATIVE SPACE for later text overlay, professional thumbnail design, "
        "NO TEXT, NO WORDS"
    )

    if strategy["prompt_mode"] == "background_only":
        subject = "BACKGROUND ONLY, no people, no face, no human, no hands, no character"
        neg = BASE_NEGATIVE + ", person, people, human, face, portrait, selfie, eye, eyes, closeup, macro"
    else:
        subject = "one person, medium close-up, looking at camera, natural proportions"
        neg = BASE_NEGATIVE + ", closeup, macro"

    positive = ", ".join([
        composition,
        style_p, emo_p, col_p,
        cues,
        subject,
        f"topic: {title}" if title else ""
    ]).strip(", ").strip()

    return {"positive": positive, "negative": neg}

def rel_url(path: str) -> str:
    if BASE_URL:
        return f"{BASE_URL}{path}"
    return path

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/", response_class=HTMLResponse)
def intake_form():
    cfg = load_cfg()
    niche_opts = "".join([f'<option value="{k}">{k}</option>' for k in cfg["niches"].keys()])
    arch_opts = "".join([f'<option value="{k}">{k}</option>' for k in cfg["archetypes"].keys()])
    emo_opts = "".join([f'<option value="{k}">{k}</option>' for k in cfg["emotions"].keys()])
    style_opts = "".join([f'<option value="{k}">{k}</option>' for k in cfg["styles"].keys()])
    color_opts = '<option value="AUTO">AUTO</option>' + "".join([f'<option value="{k}">{k}</option>' for k in cfg["color_themes"].keys()])

    return f"""
    <html><body style="font-family:Arial;max-width:920px;margin:25px auto;">
      <h2>Thumbnail Intake Form</h2>
      <form action="/submit" method="post" enctype="multipart/form-data">
        <label>Agent Name</label><br><input name="agent_name" required style="width:100%"><br><br>
        <label>Client Name</label><br><input name="client_name" required style="width:100%"><br><br>
        <label>Client Contact</label><br><input name="client_contact" required style="width:100%"><br><br>

        <label>Video Title</label><br><input name="video_title" required style="width:100%"><br><br>

        <label>Niche (optional)</label><br>
        <select name="niche" style="width:100%">
          <option value="">AUTO</option>
          {niche_opts}
        </select><br><br>

        <label>Archetype</label><br>
        <select name="archetype" style="width:100%">{arch_opts}</select><br><br>

        <label>Emotion</label><br>
        <select name="emotion" style="width:100%">{emo_opts}</select><br><br>

        <label>Style</label><br>
        <select name="style" style="width:100%">{style_opts}</select><br><br>

        <label>Color Theme</label><br>
        <select name="color_theme" style="width:100%">{color_opts}</select><br><br>

        <label>Thumbnail Text (optional)</label><br><input name="thumb_text" style="width:100%"><br><br>

        <label>How many options?</label><br>
        <select name="options">
          <option value="3">3</option>
          <option value="5" selected>5</option>
          <option value="10">10</option>
          <option value="20">20</option>
        </select><br><br>

        <label>Size</label><br>
        <input name="w" value="1344"> x <input name="h" value="768"><br><br>

        <label>Face image (optional)</label><br><input type="file" name="face"><br><br>
        <label>Logo (optional)</label><br><input type="file" name="logo"><br><br>
        <label>Reference image (optional)</label><br><input type="file" name="ref_img"><br><br>

        <button type="submit">Submit Job</button>
      </form>
      <hr>
      <p>Results will appear on the job page link.</p>
    </body></html>
    """

@app.post("/submit", response_class=HTMLResponse)
async def submit_form(
    agent_name: str = Form(...),
    client_name: str = Form(...),
    client_contact: str = Form(...),
    video_title: str = Form(...),
    niche: str = Form(""),
    archetype: str = Form("AUTO"),
    emotion: str = Form("AUTO"),
    style: str = Form("photoreal"),
    color_theme: str = Form("AUTO"),
    thumb_text: str = Form(""),
    options: int = Form(5),
    w: int = Form(1344),
    h: int = Form(768),
    face: UploadFile | None = File(None),
    logo: UploadFile | None = File(None),
    ref_img: UploadFile | None = File(None),
):
    job_id = "JOB_" + uuid.uuid4().hex[:12].upper()
    d = job_dir(job_id)
    uploads = d / "uploads"
    results = d / "results"
    uploads.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    async def save_up(f: UploadFile | None, name: str):
        if not f or not f.filename:
            return None
        out = uploads / name
        out.write_bytes(await f.read())
        return out.name

    face_name = await save_up(face, "face.png")
    logo_name = await save_up(logo, "logo.png")
    ref_name  = await save_up(ref_img, "ref.jpg")

    job = {
        "job_id": job_id,
        "status": "queued",
        "created_at": now_iso(),
        "claimed_at": None,
        "done_at": None,
        "agent": {"name": agent_name},
        "client": {"name": client_name, "contact": client_contact},
        "video": {"title": video_title, "niche": niche},
        "creative": {
            "archetype": archetype,
            "emotion": emotion,
            "style": style,
            "color_theme": color_theme,
            "thumb_text": thumb_text,
            "options": int(options),
            "size": {"w": int(w), "h": int(h)}
        },
        "uploads": {"face": face_name, "logo": logo_name, "ref_img": ref_name},
        "results": {"zip": None, "preview": None},
        "computed": {"strategy": None, "prompts": None}
    }
    write_job(job_id, job)

    url = rel_url(f"/job/{job_id}")
    return f"""
    <html><body style="font-family:Arial;max-width:820px;margin:25px auto;">
      <h3>✅ Submitted</h3>
      <p><b>Job ID:</b> {job_id}</p>
      <p><a href="{url}">{url}</a></p>
    </body></html>
    """

@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(job_id: str):
    job = read_job(job_id)
    z = job["results"]["zip"]
    p = job["results"]["preview"]
    status = job["status"]
    zip_link = rel_url(f"/download/{job_id}/zip") if z else ""
    prev_link = rel_url(f"/download/{job_id}/preview") if p else ""

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

@app.get("/api/next_job")
def api_next_job(x_api_key: str | None = Header(default=None)):
    require_key(x_api_key)
    cfg = load_cfg()

    jobs = sorted([p for p in DATA_DIR.iterdir() if p.is_dir()])
    for d in jobs:
        job = read_job(d.name)
        if job["status"] == "queued":
            job["status"] = "processing"
            job["claimed_at"] = now_iso()

            strategy = build_strategy(cfg, job)
            prompts = build_prompts(cfg, job, strategy)
            job["computed"]["strategy"] = strategy
            job["computed"]["prompts"] = prompts

            write_job(job["job_id"], job)

            up = job["uploads"]
            def up_url(name):
                return f"/api/job/{job['job_id']}/upload/{name}" if name else None

            return {
                "job": job,
                "upload_urls": {
                    "face": up_url(up.get("face")),
                    "logo": up_url(up.get("logo")),
                    "ref_img": up_url(up.get("ref_img"))
                },
                "strategy": strategy,
                "prompts": prompts
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
