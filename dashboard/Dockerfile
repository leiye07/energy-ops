FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /dashboard
COPY --chown=10001:10001 . .
USER 10001:10001
CMD ["python","server.py"]
