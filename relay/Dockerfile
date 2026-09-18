# Minimal stdlib-only relay image.
# Base image pinned to an explicit OS variant tag; bump this tag on purpose
# when moving to a newer Python (no floating `slim` tag, no extra packages).
FROM python:3.14-slim-bookworm
WORKDIR /app
COPY server.py ./server.py
ENV PORT=8099
EXPOSE 8099
CMD ["python3", "server.py"]
