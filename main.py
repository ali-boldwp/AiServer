import os, json, uuid, datetime
from pathlib import Path
from fastapi import FastAPI, Form, File, UploadFile, Header, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DATA_DIR = Path(os.environ["DATA_DIR"])
API_KEY = os.environ["API_KEY"]
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

DATA_DIR.mkdir(parents=True, exist_ok=True)

def require_key(x_api_key: str | None):
    if x_api_key != API_KEY:
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

app = FastAPI()

# -----------------------
# Web intake form (agents)
# -----------------------
@app.get("/", response_class=HTMLResponse)
def intake_form():
    return """
    <html><body style="font-family:Arial;max-width:820px;margin:25px auto;">
      <h2>Thumbnail Intake Form</h2>
      <form action="/submit" method="post" enctype="multipart/form-data">
        <label>Agent Name</label><br><input name="agent_name" required style="width:100%"><br><br>
        <label>Client Name</label><br><input name="client_name" required style="width:100%"><br><br>
        <label>Client Contact (Discord/Email)</label><br><input name="client_contact" required style="width:100%"><br><br>
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
      <hr>
      <p>Agent can later download results from the link returned after submit.</p>
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
    uploads = d / "uploads"
    results = d / "results"
    uploads.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    async def save_up(f: UploadFile | None, name: str):
        if not f or not f.filename:
            return None
        out = uploads / name
        out.write_bytes(await f.read())
        return str(out.name)

    face_name = save_up(face, "face.png")
    logo_name = save_up(logo, "logo.png")
    ref_name  = save_up(ref_img, "ref.jpg")

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
            "emotion": emotion,
            "style": style,
            "thumb_text": thumb_text,
            "ref_links": ref_links,
            "options": int(options),
            "size": {"w": 1344, "h": 768}
        },
        "uploads": {"face": face_name, "logo": logo_name, "ref_img": ref_name},
        "results": {"zip": None, "preview": None}
    }
    write_job(job_id, job)

    download_url = f"{BASE_URL}/job/{job_id}" if BASE_URL else f"/job/{job_id}"
    return f"""
    <html><body style="font-family:Arial;max-width:820px;margin:25px auto;">
      <h3>✅ Submitted</h3>
      <p><b>Job ID:</b> {job_id}</p>
      <p>Result page:</p>
      <p><a href="{download_url}">{download_url}</a></p>
      <p>(Your laptop will pick it up automatically and upload ZIP here.)</p>
    </body></html>
    """

# -----------------------
# Result page for agents
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
# Laptop API (secure)
# -----------------------
@app.get("/api/next_job")
def api_next_job(x_api_key: str | None = Header(default=None)):
    require_key(x_api_key)

    # find oldest queued job
    jobs = sorted([p for p in DATA_DIR.iterdir() if p.is_dir()])
    for d in jobs:
        job = read_job(d.name)
        if job["status"] == "queued":
            job["status"] = "processing"
            job["claimed_at"] = now_iso()
            write_job(job["job_id"], job)

            # Provide download URLs for uploads
            up = job["uploads"]
            def up_url(name):
                return f"/api/job/{job['job_id']}/upload/{name}" if name else None

            payload = {
                "job": job,
                "upload_urls": {
                    "face": up_url(up.get("face")),
                    "logo": up_url(up.get("logo")),
                    "ref_img": up_url(up.get("ref_img"))
                }
            }
            return payload

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
    preview: UploadFile | None = File(None)
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
