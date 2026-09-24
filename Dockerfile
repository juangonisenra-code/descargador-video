FROM python:3.12-slim-bookworm
 
# ffmpeg lo usa yt-dlp para unir audio+video cuando hace falta.
# Se instala desde PyPI y NO desde apt, porque la red de construccion de
# Render devuelve 501 al pedir los repositorios de Debian.
RUN pip install --no-cache-dir \
        yt-dlp gallery-dl flask gunicorn requests imageio-ffmpeg \
    && ln -s "$(python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" /usr/local/bin/ffmpeg
 
WORKDIR /app
COPY app.py /app/app.py
 
# Render (y otros) inyectan el puerto en $PORT; si no, 8080
ENV PORT=8080
EXPOSE 8080
 
# timeout amplio porque descargar puede tardar
CMD ["sh","-c","gunicorn -b 0.0.0.0:${PORT:-8080} -t 900 -w 2 app:app"]
 
