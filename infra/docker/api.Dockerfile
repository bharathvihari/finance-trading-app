FROM python:3.12-slim

WORKDIR /app

COPY apps/api/requirements.txt /tmp/api-requirements.txt
COPY apps/workers/requirements.txt /tmp/workers-requirements.txt
RUN pip install --no-cache-dir -r /tmp/api-requirements.txt -r /tmp/workers-requirements.txt

COPY . /app

WORKDIR /app/apps/api
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
