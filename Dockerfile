FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTF1_CACHE_PATH=/var/lib/racepulse/fastf1-cache

WORKDIR /app

# libgomp is needed by common NumPy/FastF1 wheels on slim Debian images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 racepulse \
    && useradd --uid 10001 --gid racepulse --create-home racepulse

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY run.py ./

RUN mkdir -p /var/lib/racepulse/fastf1-cache \
    && chown -R racepulse:racepulse /app /var/lib/racepulse

USER racepulse

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
