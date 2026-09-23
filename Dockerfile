FROM python:3.12-slim

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install --no-cache-dir --upgrade pip setuptools \
 && pip install --no-cache-dir .

COPY policy.yaml .env.example ./

ENV CLEARVIEW_HOST=0.0.0.0 \
    CLEARVIEW_PORT=8000 \
    CLEARVIEW_DB_PATH=/data/clearview.db \
    CLEARVIEW_POLICY_PATH=/app/policy.yaml

# Run as a non-root user; create the data directory first so a named volume
# mount inherits the correct ownership when no host dir is bind-mounted.
RUN addgroup --system clearview \
 && adduser --system --ingroup clearview clearview \
 && mkdir -p /data && chown clearview:clearview /data

USER clearview
VOLUME /data
EXPOSE 8000

CMD ["python", "-m", "app"]
