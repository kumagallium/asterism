# Asterism auth gate — stdlib-only session-cookie login (no third-party deps).
# Self-contained build context (this dir only).
#   docker build -f infra/authgate/Dockerfile -t asterism-authgate infra/authgate
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY app.py /app/app.py
EXPOSE 9000
# Run as a non-root user — this is the front-line auth boundary.
RUN useradd --system --no-create-home authgate
USER authgate
CMD ["python", "/app/app.py"]
