FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
RUN useradd --create-home appuser && mkdir -p /app/data/private && chown -R appuser:appuser /app
USER appuser
EXPOSE 8010
CMD ["uv", "run", "--frozen", "shipping-agent", "serve", "--host", "0.0.0.0", "--port", "8010"]
