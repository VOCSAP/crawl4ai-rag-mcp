FROM python:3.12-slim

ARG PORT=8051

WORKDIR /app

# System deps required by Playwright/Chromium on slim images
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Copy the MCP server files
COPY . .

# Install CPU-only torch first to prevent sentence-transformers from pulling CUDA variant (~2 GB)
RUN uv pip install --system torch --index-url https://download.pytorch.org/whl/cpu

# Install packages directly to the system (no virtual environment)
# Combining commands to reduce Docker layers
RUN uv pip install --system -e . && \
    crawl4ai-setup

# Bake the local reranker CrossEncoder model into the image so HF_HUB_OFFLINE=1
# works at runtime with no host-side cache. This survives `--build`, `down`,
# and `git pull` (the model is part of the image artifact, not a bind mount).
# Changing the runtime RERANKING_MODEL requires a rebuild with a matching
# --build-arg RERANKING_MODEL=...
ENV HF_HOME=/app/hf_cache
ARG RERANKING_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('${RERANKING_MODEL}')"

EXPOSE ${PORT}

# Command to run the MCP server
CMD ["python", "src/crawl4ai_mcp.py"]
