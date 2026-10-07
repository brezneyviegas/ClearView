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

RUN addgroup --system --gid 1001 clearview && \
    adduser --system --uid 1001 --ingroup clearview --no-create-home clearview && \
    mkdir -p /data && chown -R 1001:1001 /data

VOLUME /data
EXPOSE 8000

USER clearview
CMD ["python", "-m", "app"]
