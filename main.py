import os, glob, uuid, time, shutil, tempfile, threading
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
BASE = tempfile.gettempdir()
JOBS = {}
VFMT, AFMT = {"mp4", "webm", "mkv"}, {"mp3", "m4a", "flac", "opus"}


class Req(BaseModel):
    url: str
    kind: str = "video"
    height: int = 720
    fmt: str = "mp4"
    abr: int = 192


@app.get("/")
def root():
    return {"ok": True}


@app.post("/api/info")
def info(r: Req):
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as y:
            d = y.extract_info(r.url, download=False)
    except Exception as e:
        raise HTTPException(400, "تعذر تحليل الرابط: " + str(e)[:160])
    hs = sorted({f["height"] for f in d.get("formats", []) if f.get("height") and f.get("vcodec") != "none"})
    return {"title": d.get("title"), "duration": d.get("duration"), "thumbnail": d.get("thumbnail"),
            "site": d.get("extractor_key"), "heights": hs}


def work(jid, r):
    j = JOBS[jid]
    d = os.path.join(BASE, jid)
    os.makedirs(d, exist_ok=True)

    def hook(h):
        if h["status"] == "downloading":
            t = h.get("total_bytes") or h.get("total_bytes_estimate") or 0
            if t:
                j["p"] = int(h["downloaded_bytes"] / t * 85)

    o = {"quiet": True, "noplaylist": True, "outtmpl": d + "/%(title).80s.%(ext)s", "progress_hooks": [hook]}
    if r.kind == "audio":
        o["format"] = "bestaudio/best"
        o["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": r.fmt, "preferredquality": str(r.abr)}]
    else:
        h = r.height
        o["format"] = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
        o["merge_output_format"] = r.fmt
    try:
        j["s"] = "downloading"
        with yt_dlp.YoutubeDL(o) as y:
            y.download([r.url])
        j.update(s="done", p=100, file=glob.glob(d + "/*")[0])
    except Exception as e:
        j.update(s="error", err=str(e)[:160])


@app.post("/api/start")
def start(r: Req):
    if r.fmt not in (AFMT if r.kind == "audio" else VFMT):
        raise HTTPException(400, "صيغة غير مدعومة")
    for k in [k for k, v in JOBS.items() if time.time() - v["t"] > 3600]:
        shutil.rmtree(os.path.join(BASE, k), ignore_errors=True)
        JOBS.pop(k, None)
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {"s": "queued", "p": 0, "t": time.time()}
    threading.Thread(target=work, args=(jid, r), daemon=True).start()
    return {"id": jid}


@app.get("/api/status/{jid}")
def status(jid: str):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404, "المهمة غير موجودة")
    return {k: v for k, v in j.items() if k not in ("file", "t")}


@app.get("/api/file/{jid}")
def file(jid: str):
    j = JOBS.get(jid)
    if not j or j.get("s") != "done":
        raise HTTPException(404, "الملف غير جاهز")
    return FileResponse(j["file"], filename=os.path.basename(j["file"]))
