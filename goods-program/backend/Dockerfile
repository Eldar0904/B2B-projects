# Development image: installs deps and runs uvicorn with --reload.
# Source is bind-mounted over this in docker-compose.yml for hot reload,
# so COPY here mainly matters for `docker build` without compose.
FROM python:3.11-slim

WORKDIR /app

# psycopg2-binary ships its own libpq, but build-essential covers any
# package here that needs to compile a C extension from source on an
# architecture without a prebuilt wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-embeddings.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Multilingual embedding model (LaBSE) - ENABLED (30 July 2026, NEXT_STEPS.md
# item 4: the single biggest documented accuracy gap was TF-IDF's inability
# to bridge Kazakh <-> Russian at all, confirmed as a real, repeatable
# failure mode in HANDOFF.md section 12.2's cross-check).
#
# This pulls torch plus a 1.8 GB model download, which pushed the build
# past 20 minutes the first time this was tried (looked like a hang around
# step 23 - it is not hung, just slow on the first build only; the model is
# cached in the image layer afterward). The app runs fine even if this step
# fails for some reason (no network on first build, etc.) -
# build_embedding_provider() falls back to TF-IDF automatically with a
# loud warning (see search/embeddings.py) rather than refusing to start.
#
# CPU-only torch, installed BEFORE requirements-embeddings.txt on purpose:
# a plain `pip install sentence-transformers` resolves torch's default PyPI
# wheel, which bundles the full NVIDIA CUDA toolkit as separate dependencies
# (confirmed via a dry-run while making this change - it pulls in a dozen-
# plus `nvidia-cu*` packages on top of torch itself, several GB beyond the
# "~2.5 GB" this comment originally quoted). This container has no GPU to
# use any of that for, so it is pure wasted download/build time. Installing
# the CPU-only build from PyTorch's own index first satisfies
# sentence-transformers' `torch>=1.11.0` requirement, so the second
# `pip install` below sees torch already present and never reaches for the
# CUDA version.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
#
# Every other v2 fix is unaffected: the BM25 normalization, the IDF
# coverage signal, the fuzzy scorer, the recalibrated thresholds and the
# removal of silent auto-rejection are all pure Python.
#
# TO DISABLE AGAIN: re-comment the RUN lines in this block and set
# EMBEDDING_PROVIDER=tfidf in backend/.env, then `docker compose up --build`.
RUN pip install --no-cache-dir -r requirements-embeddings.txt
ENV HF_HOME=/opt/hf
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/LaBSE')" \
    || echo "WARNING: could not pre-download LaBSE; it will be fetched at runtime."

COPY . .
RUN chmod +x entrypoint.sh

EXPOSE 8000

# entrypoint.sh runs migrations once, then execs uvicorn - see that
# script's own comment for why this replaced running migrations from
# inside app/main.py's startup event (HANDOFF.md section 14.0).
#
# Invoked via "sh entrypoint.sh" rather than "./entrypoint.sh": the RUN
# chmod +x above only applies to the image's own copy of this file, but
# docker-compose.yml bind-mounts ./backend over /app at runtime, and a
# Windows bind mount doesn't reliably preserve the Linux executable bit.
# Calling the interpreter explicitly doesn't depend on that bit at all.
CMD ["sh", "entrypoint.sh"]
