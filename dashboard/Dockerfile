FROM node:20-alpine AS frontend
WORKDIR /app
COPY dashboard/frontend/package.json ./
RUN npm install
COPY dashboard/frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY dashboard/backend/requirements.txt /app/dashboard/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/dashboard/backend/requirements.txt
COPY dashboard /app/dashboard
COPY incident-detection /app/incident-detection
COPY --from=frontend /app/dist /app/dashboard/frontend/dist
ENV PYTHONPATH=/app:/app/incident-detection
EXPOSE 8090
CMD ["uvicorn", "dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8090"]
