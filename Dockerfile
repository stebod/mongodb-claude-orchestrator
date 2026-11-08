FROM python:3.12

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --shell /usr/sbin/nologin agent

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY config ./config
COPY models ./models
COPY providers ./providers

ENV PYTHONUNBUFFERED=1 \
    HOME=/home/agent

USER agent

CMD ["python", "main.py"]
