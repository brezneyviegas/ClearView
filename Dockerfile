FROM python:3.12-slim

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install --no-cache-dir --upgrade pip setuptools && \
    pip install --no-cache-dir .

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

COPY policy.yaml .env.example ./

ENV CLEARVIEW_HOST=0.0.0.0 \
    CLEARVIEW_PORT=8000 \
    CLEARVIEW_DB_PATH=/data/clearview.db \
    CLEARVIEW_POLICY_PATH=/app/policy.yaml

VOLUME /data
EXPOSE 8000

USER appuser
CMD ["python", "-m", "app"]
