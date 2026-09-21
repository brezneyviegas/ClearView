FROM python:3.12-slim

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install --no-cache-dir --upgrade "pip>=26.2.1" "setuptools>=83.0.0" \
 && pip install --no-cache-dir .

COPY policy.yaml .env.example ./

ENV CLEARVIEW_HOST=0.0.0.0 \
    CLEARVIEW_PORT=8000 \
    CLEARVIEW_DB_PATH=/data/clearview.db \
    CLEARVIEW_POLICY_PATH=/app/policy.yaml

RUN useradd -r -s /bin/false clearview \
 && mkdir -p /data \
 && chown clearview /data

VOLUME /data
EXPOSE 8000

USER clearview

CMD ["python", "-m", "app"]
