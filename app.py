"""
Descargador para El Periódico de Ceuta.
Dos endpoints HTTP:
  POST /fbvideo  -> recibe una URL de Facebook (reel/vídeo) y devuelve el mp4.
  POST /fbfotos  -> recibe una URL de un POST de Facebook con álbum y devuelve TODAS
                    las fotos (en base64). Usa cookies de una cuenta de Facebook.

Cookies de Facebook (para /fbfotos):
  - Se guardan en WordPress (opción epc_fb_cookies) para que sobrevivan a reinicios.
  - En cada uso se leen de WP, se usan, y se vuelven a guardar (REFRESCO RODANTE):
    así la sesión se mantiene viva sola mucho más tiempo.
  - Si caducan de verdad, /fbfotos responde {ok:false, expired:true} para que n8n te avise.

Seguridad: exige la cabecera  X-Auth: <FBV_TOKEN>  y solo acepta URLs de facebook.com.
"""
import os, re, shutil, subprocess, tempfile, base64
from io import BytesIO
from flask import Flask, request, jsonify, send_file

try:
    import requests
except Exception:
    requests = None

app = Flask(__name__)
TOKEN   = os.environ.get("FBV_TOKEN", "")            # obligatorio en producción
COOKIES = os.environ.get("FBV_COOKIES", "")          # opcional: ruta a cookies.txt local (respaldo)
MAX_MB  = int(os.environ.get("FBV_MAX_MB", "300"))   # tope de tamaño de vídeo
MAX_FOTOS = int(os.environ.get("FBV_MAX_FOTOS", "20"))  # tope de fotos por galería (límite del plugin)

# Para leer/guardar las cookies en WordPress (refresco rodante)
WP_BASE   = os.environ.get("WP_BASE", "https://www.elperiodicodeceuta.es").rstrip("/")
EPC_TOKEN = os.environ.get("EPC_TOKEN", "")          # el token del endpoint epc/v1 en WP

FB_RE = re.compile(r"^https?://([a-z0-9\-]+\.)?facebook\.com/", re.I)
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


@app.get("/health")
def health():
    v = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
    try:
        g = subprocess.run(["gallery-dl", "--version"], capture_output=True, text=True).stdout.strip()
    except Exception:
        g = "no instalado"
    return jsonify(ok=True, ytdlp=v.stdout.strip(), gallerydl=g)


# ---------- Cookies: leer/guardar en WordPress ----------
def cookies_load_from_wp(path):
    """Descarga las cookies guardadas en WP a un fichero. Devuelve True si había."""
    if not (requests and EPC_TOKEN):
        return False
    try:
        r = requests.get(f"{WP_BASE}/wp-json/epc/v1/fbcookies",
                         headers={"X-EPC-Token": EPC_TOKEN}, timeout=25)
        if r.ok and r.text.strip():
            with open(path, "w") as f:
                f.write(r.text)
            return True
    except Exception:
        pass
    return False


def cookies_save_to_wp(path):
    """Sube las cookies (posiblemente refrescadas) de vuelta a WP."""
    if not (requests and EPC_TOKEN):
        return
    try:
        with open(path) as f:
            data = f.read()
        if data.strip():
            requests.post(f"{WP_BASE}/wp-json/epc/v1/fbcookies",
                          headers={"X-EPC-Token": EPC_TOKEN, "Content-Type": "text/plain; charset=utf-8"},
                          data=data.encode("utf-8"), timeout=25)
    except Exception:
        pass


# ---------- /fbvideo (igual que antes) ----------
@app.post("/fbvideo")
def fbvideo():
    if TOKEN and request.headers.get("X-Auth", "") != TOKEN:
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not FB_RE.match(url):
        return jsonify(error="solo se aceptan URLs de facebook.com"), 400

    tmp = tempfile.mkdtemp(prefix="fbv_")
    try:
        out = os.path.join(tmp, "video.%(ext)s")
        cmd = [
            "yt-dlp",
            "-f", f"best[ext=mp4][filesize<{MAX_MB}M]/best[ext=mp4]/best",
            "--no-playlist", "--max-filesize", f"{MAX_MB}M",
            "--merge-output-format", "mp4",
            "-o", out, url,
        ]
        if COOKIES:
            cmd[1:1] = ["--cookies", COOKIES]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        files = [f for f in os.listdir(tmp) if f.startswith("video.")]
        if not files:
            return jsonify(error="descarga fallida", detail=(r.stderr or "")[-600:]), 502
        path = os.path.join(tmp, files[0])
        with open(path, "rb") as f:
            blob = f.read()
        return send_file(BytesIO(blob), mimetype="video/mp4",
                         as_attachment=True, download_name="video.mp4")
    except subprocess.TimeoutExpired:
        return jsonify(error="timeout (vídeo demasiado largo)"), 504
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- /fbfotos (nuevo: todas las fotos del álbum) ----------
@app.post("/fbfotos")
def fbfotos():
    if TOKEN and request.headers.get("X-Auth", "") != TOKEN:
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not FB_RE.match(url):
        return jsonify(error="solo se aceptan URLs de facebook.com"), 400

    tmp = tempfile.mkdtemp(prefix="fbf_")
    cookies_path = os.path.join(tmp, "cookies.txt")
    try:
        have = cookies_load_from_wp(cookies_path)
        if not have and COOKIES and os.path.exists(COOKIES):
            shutil.copy(COOKIES, cookies_path)
            have = True

        cmd = ["gallery-dl", "-q", "--no-mtime",
               "-o", "cookies-update=true",
               "-D", tmp, url]
        if have:
            cmd[1:1] = ["--cookies", cookies_path]

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        blob = ((r.stderr or "") + "\n" + (r.stdout or "")).lower()

        # ¿cookies caducadas / hace falta login?
        if ("logged in" in blob) or ("authrequired" in blob) or ("login required" in blob):
            return jsonify(ok=False, expired=True,
                           error="Facebook pide iniciar sesión: renueva las cookies."), 401

        imgs = sorted([f for f in os.listdir(tmp)
                       if f.lower().endswith(IMG_EXT) and f != "cookies.txt"])
        imgs = imgs[:MAX_FOTOS]

        # refresco rodante: guardamos las cookies (posiblemente actualizadas)
        cookies_save_to_wp(cookies_path)

        if not imgs:
            return jsonify(ok=False, count=0,
                           error="no se encontraron fotos",
                           detail=(r.stderr or "")[-500:]), 502

        photos = []
        for fn in imgs:
            with open(os.path.join(tmp, fn), "rb") as f:
                photos.append({"filename": fn,
                               "b64": base64.b64encode(f.read()).decode("ascii")})
        return jsonify(ok=True, count=len(photos), photos=photos)
    except subprocess.TimeoutExpired:
        return jsonify(ok=False, error="timeout"), 504
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
