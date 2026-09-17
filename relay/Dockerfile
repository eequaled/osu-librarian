# Minimal stdlib-only relay image.
FROM python:3.14-slim
WORKDIR /app
COPY server.py ./server.py
ENV PORT=8099
EXPOSE 8099
CMD ["python3", "server.py"]
