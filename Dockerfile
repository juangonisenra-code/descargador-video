FROM python:3.12-slim

# ffmpeg lo usa yt-dlp para unir audio+vídeo cuando hace falta
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir yt-dlp gallery-dl flask gunicorn requests

WORKDIR /app
COPY app.py /app/app.py

# Render (y otros) inyectan el puerto en $PORT; si no, 8080
ENV PORT=8080
EXPOSE 8080

# timeout amplio porque descargar puede tardar
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT:-8080} -t 330 -w 2 app:app"]
