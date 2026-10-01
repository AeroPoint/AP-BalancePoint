# BalancePoint in a container. Data lives in the /data volume; see README "Run it with Docker".
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BUDGET_DATA_DIR=/data \
    BUDGET_HOST=0.0.0.0

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home --uid 1000 budget \
    && mkdir -p /data /demo \
    && chown budget:budget /data /demo
USER budget

VOLUME ["/data"]
EXPOSE 5000

# Listens on every network inside the container; the app still answers only trusted addresses
# (add Docker's with BUDGET_TRUSTED_NETWORKS) and asks for BUDGET_PASSWORD if one is set.
CMD ["python", "run.py", "serve", "--no-browser", "--host", "0.0.0.0", "--port", "5000"]
