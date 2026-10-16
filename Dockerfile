FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 10001 -m app
COPY --chown=app:app app /app/app
USER app
CMD ["uvicorn","app.api:app","--host","0.0.0.0","--port","8000","--no-access-log"]
