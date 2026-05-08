FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY main.py /app/main.py
COPY oi_tracker.py /app/oi_tracker.py
COPY ui.py /app/ui.py
COPY start.sh /app/start.sh

RUN mkdir -p /app/data \
    && chmod +x /app/start.sh

EXPOSE 8501

CMD ["/app/start.sh"]
