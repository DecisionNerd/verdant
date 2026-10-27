# Routing qualification image only; not the platform deployment image.
FROM ghcr.io/astral-sh/uv:0.8.16-python3.12-bookworm-slim@sha256:190d69899a7a73b22460b2779f85d2d65fddf9bcb05037d4463beaf52310a919
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --locked --no-dev
COPY scripts/qualify_issuer_route.py ./probe.py
CMD ["uv", "run", "--no-sync", "python", "probe.py"]
