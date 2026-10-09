FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# One worker: the event loop handles concurrency; scale out with more replicas/containers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
