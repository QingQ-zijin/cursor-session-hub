FROM node:22-bookworm-slim AS web
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CSH_MODE=cloud CSH_HOME=/data CSH_FRONTEND_DIR=/app/frontend/dist
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home hub && mkdir /data && chown hub:hub /data
COPY hub/ ./hub/
COPY cursor_parser.py cursor_binary.py common.py event_schema.py ./
COPY --from=web /build/dist ./frontend/dist
USER hub
EXPOSE 8000
CMD ["python", "-m", "hub", "serve", "--mode", "cloud", "--host", "0.0.0.0", "--port", "8000"]
