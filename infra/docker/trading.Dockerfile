FROM python:3.12-slim

WORKDIR /app

COPY apps/trading/requirements.txt /tmp/trading-requirements.txt
RUN pip install --no-cache-dir -r /tmp/trading-requirements.txt

COPY . /app

WORKDIR /app/apps/trading
CMD ["python", "-m", "nautilus_runner.main"]
