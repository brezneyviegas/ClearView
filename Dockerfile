FROM python:3.12-slim

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY policy.yaml .env.example ./

ENV CLEARVIEW_HOST=0.0.0.0 \
    CLEARVIEW_PORT=8000 \
    CLEARVIEW_DB_PATH=/data/clearview.db \
    CLEARVIEW_POLICY_PATH=/app/policy.yaml

RUN addgroup --system cv && adduser --system --ingroup cv cv && \
    mkdir -p /data && chown cv:cv /data

VOLUME /data
EXPOSE 8000

USER cv

CMD ["python", "-m", "app"]
