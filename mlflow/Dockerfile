FROM python:3.12.12-slim-bookworm
RUN pip install --no-cache-dir mlflow==3.17.1 psycopg2-binary==2.9.11 && useradd -u 10001 -m app
COPY start.py /start.py
USER app
CMD ["python","/start.py"]
