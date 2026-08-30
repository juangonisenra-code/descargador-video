"""
Descargador de vídeo para El Periódico de Ceuta.
Un endpoint HTTP que recibe una URL de Facebook (reel/vídeo) y devuelve el mp4.
Lo llama n8n cuando apruebas una noticia con vídeo.

Seguridad: exige la cabecera  X-Auth: <FBV_TOKEN>  y solo acepta URLs de facebook.com.
Ponlo SIEMPRE detrás de HTTPS (tu dominio) y con un token largo y secreto.
"""
import os, re, shutil, subprocess, tempfile
from io import BytesIO
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)
TOKEN = os.environ.get("FBV_TOKEN", "")            # obligatorio en producción
COOKIES = os.environ.get("FBV_COOKIES", "")        # opcional: ruta a cookies.txt de FB
MAX_MB = int(os.environ.get("FBV_MAX_MB", "300"))  # tope de tamaño
FB_RE = re.compile(r"^https?://([a-z0-9\-]+\.)?facebook\.com/", re.I)


@app.get("/health")
def health():
    v = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
    return jsonify(ok=True, ytdlp=v.stdout.strip())


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
