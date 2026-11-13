"""喬山 SOP 系統 v2（Claude）— FastAPI 入口。

啟動： uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import llm, services

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("sop.app")

STATIC = Path(__file__).parent / "static"
WORK = Path(os.environ.get("SOP_WORK_DIR") or tempfile.mkdtemp(prefix="sop-jobs-"))
WORK.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024
JOB_TTL = int(os.environ.get("JOB_TTL_HOURS", "6")) * 3600
ALLOWED = {".docx", ".pdf", ".doc", ".txt", ".md"}
SOP_TYPES = {"自動判定", "Group Critical SOP", "Affiliate Critical SOP", "HQ Local SOP", "Affiliate Local SOP"}

app = FastAPI(title="喬山 SOP 自評與審核系統 v2", docs_url=None, redoc_url=None)
jobs: dict[str, dict] = {}
lock = threading.Lock()
pool = ThreadPoolExecutor(max_workers=int(os.environ.get("MAX_CONCURRENT_JOBS", "2")))
basic = HTTPBasic(auto_error=False)


def auth(cred: HTTPBasicCredentials | None = Depends(basic)):
    """設定 APP_PASSWORD 後啟用帳密保護（帳號預設 sop）。公司文件請務必啟用。"""
    password = os.environ.get("APP_PASSWORD")
    if not password:
        return
    user = os.environ.get("APP_USER", "sop")
    ok = cred is not None and secrets.compare_digest(cred.username.encode(), user.encode()) \
        and secrets.compare_digest(cred.password.encode(), password.encode())
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "需要登入", headers={"WWW-Authenticate": "Basic"})


def _cleanup():
    now = time.time()
    with lock:
        for jid in [j for j, v in jobs.items() if now - v["created"] > JOB_TTL]:
            shutil.rmtree(WORK / jid, ignore_errors=True)
            jobs.pop(jid, None)


async def _save(upload: UploadFile, folder: Path) -> Path:
    name = Path(upload.filename or "upload").name
    if Path(name).suffix.lower() not in ALLOWED:
        raise HTTPException(400, f"不支援的檔案格式：{name}（請上傳 DOCX、PDF、DOC 或 TXT）")
    data = await upload.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, f"{name} 超過 {MAX_UPLOAD // 1024 // 1024} MB 上限")
    if not data:
        raise HTTPException(400, f"{name} 是空檔案")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def _start(fn, **kwargs) -> dict:
    _cleanup()
    jid = kwargs.pop("job_id")
    job = {"id": jid, "status": "running", "progress": "排隊中", "created": time.time(), "result": None, "error": None}
    with lock:
        jobs[jid] = job

    def run():
        try:
            job["result"] = fn(progress=lambda m: job.__setitem__("progress", m), **kwargs)
            job["status"] = "done"
        except (llm.LLMError, ValueError) as e:
            job["status"], job["error"] = "error", str(e)
        except Exception:
            log.exception("job %s failed", jid)
            job["status"], job["error"] = "error", "處理時發生未預期的錯誤，請查看伺服器紀錄。"

    pool.submit(run)
    return {"job_id": jid}


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    return {"ok": True, "model": llm.MODEL, "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))}


@app.post("/api/review", dependencies=[Depends(auth)])
async def review(file: UploadFile | None = File(None), mode: str = Form("audit"), sop_type: str = Form("自動判定"),
                 notes: str = Form(""), from_job: str = Form("")):
    if mode not in ("audit", "self"):
        raise HTTPException(400, "mode 必須為 audit 或 self")
    if sop_type not in SOP_TYPES:
        raise HTTPException(400, "SOP 類別無效")
    jid = uuid.uuid4().hex
    folder = WORK / jid
    if from_job:  # 對剛生成的草稿直接自評
        src = jobs.get(from_job)
        if not src or src["status"] != "done" or src["result"]["kind"] != "draft":
            raise HTTPException(404, "找不到可自評的草稿")
        folder.mkdir(parents=True)
        path = Path(shutil.copy(WORK / from_job / src["result"]["files"][0], folder))
    elif file is not None:
        path = await _save(file, folder)
    else:
        raise HTTPException(400, "請上傳 SOP 檔案")
    return _start(services.run_review, job_id=jid, path=path, out_dir=folder, mode=mode, sop_type=sop_type, notes=notes[:4000])


@app.post("/api/draft", dependencies=[Depends(auth)])
async def draft(title: str = Form(...), sop_type: str = Form(...), department: str = Form(""),
                instructions: str = Form(""), ref_text: str = Form(""), control_number: str = Form(""),
                prepared_by: str = Form(""), approved_by: str = Form(""), files: list[UploadFile] = File(default=[])):
    if sop_type not in SOP_TYPES - {"自動判定"}:
        raise HTTPException(400, "請選擇 SOP 類別")
    if not title.strip():
        raise HTTPException(400, "請填寫 SOP 主題")
    files = [f for f in files if f.filename]
    if not files and not ref_text.strip() and not instructions.strip():
        raise HTTPException(400, "請至少提供參考資料檔案、貼上的文字或補充說明其中一項")
    if len(files) > 10:
        raise HTTPException(400, "參考資料最多 10 個檔案")
    jid = uuid.uuid4().hex
    folder = WORK / jid
    folder.mkdir(parents=True)
    paths = [await _save(f, folder / "refs") for f in files]
    meta = {"control_number": control_number.strip(), "prepared_by": prepared_by.strip(), "approved_by": approved_by.strip()}
    return _start(services.run_draft, job_id=jid, ref_paths=paths, out_dir=folder, title=title.strip(), sop_type=sop_type,
                  department=department, instructions=instructions[:8000], ref_text=ref_text, meta=meta)


@app.get("/api/jobs/{jid}", dependencies=[Depends(auth)])
def job_status(jid: str):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "找不到此工作（可能已過期或伺服器已重新啟動）")
    return {k: job[k] for k in ("id", "status", "progress", "result", "error")}


@app.get("/api/jobs/{jid}/files/{name}", dependencies=[Depends(auth)])
def job_file(jid: str, name: str):
    job = jobs.get(jid)
    if not job or job["status"] != "done" or name not in job["result"]["files"]:
        raise HTTPException(404, "找不到檔案")
    return FileResponse(WORK / jid / name, filename=name)
