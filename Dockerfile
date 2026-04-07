# =============================================================================
# WHAT IS THIS FILE?
#
# A Dockerfile is a recipe that tells Docker how to build a "image" —
# a self-contained, portable snapshot of everything this project needs to run:
# the right Python version, ffmpeg, all pip packages, and our source code.
#
# You only build the image once (or when dependencies change). After that you
# just spin up a "container" from it — a live, isolated process that runs
# exactly the same way on every machine, independent of what's installed on
# the host.
#
# BASE IMAGE CHOICE
# -----------------
# We start from NVIDIA's official CUDA 12.1 + cuDNN 8 image on Ubuntu 22.04.
# This gives us pre-built GPU libraries so faster-whisper can use CUDA
# without any extra setup on our part.
#
# If you DON'T have an NVIDIA GPU (or haven't set up the NVIDIA Container
# Toolkit), the container still works — faster-whisper will automatically
# fall back to CPU because config.yaml has "device: auto".  You just won't
# get GPU acceleration.
# =============================================================================
FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

# ----- System labels (optional metadata) -------------------------------------
LABEL maintainer="Local-ASP Meeting Notes Taker"
LABEL description="Whisper + Claude + Notion meeting notes pipeline"

# ----- Prevent interactive prompts during apt installs -----------------------
# Ubuntu's package installer sometimes pauses to ask timezone questions etc.
# Setting DEBIAN_FRONTEND=noninteractive silences all of that.
ENV DEBIAN_FRONTEND=noninteractive

# ----- Install system dependencies -------------------------------------------
# We need:
#   python3.11        — the Python runtime
#   python3-pip       — Python package installer
#   ffmpeg            — audio/video decoder that faster-whisper relies on
#                       to read .m4a / .mp3 / .mp4 / etc.
#   curl              — used by pip internally; good to have
#   ca-certificates   — so Python's HTTPS calls (to Anthropic / Notion APIs)
#                       can verify SSL certificates
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3-pip \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*
#   ↑ cleaning the apt cache keeps the image smaller

# Make "python" and "pip" point to python3.11 (Ubuntu defaults to python3)
RUN ln -sf /usr/bin/python3.11 /usr/bin/python && \
    ln -sf /usr/bin/pip3       /usr/bin/pip

# ----- Set the working directory ---------------------------------------------
# All subsequent commands and the running container operate from /app.
# Think of this as "cd /app" that persists for the rest of the Dockerfile.
WORKDIR /app

# ----- Install Python dependencies -------------------------------------------
# We copy requirements.txt FIRST (before the rest of the source code).
# Why?  Docker caches each step. If we copy everything first and then run pip,
# every tiny code change invalidates the pip cache, forcing a full reinstall.
# By copying just requirements.txt first, Docker only re-runs pip when
# requirements.txt actually changes — which is rare.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
#   --no-cache-dir: don't store the download cache inside the image (saves space)

# ----- Copy application source -----------------------------------------------
# Only copy what the container needs to run. We deliberately do NOT copy:
#   .env            — secrets; injected at runtime via --env-file
#   meetings/       — audio files; mounted as a volume at runtime
#   config/         — copied so it's baked in, but can be overridden by a
#                     volume mount if you want to tweak settings without rebuilding
COPY src/    ./src/
COPY config/ ./config/

# ----- Whisper model cache location ------------------------------------------
# faster-whisper downloads the model on first use and caches it in
# ~/.cache/huggingface.  We declare this as a VOLUME hint so docker-compose
# can mount a named volume there — meaning the model survives container
# restarts and you don't re-download several GB every time.
VOLUME ["/root/.cache/huggingface"]

# ----- meetings/ mount point --------------------------------------------------
# We declare /app/meetings as a volume mount point. At runtime, docker-compose
# maps ./meetings on your host to /app/meetings in the container, so:
#   - you drop audio files into meetings/inbox/ on your host as usual
#   - the container reads them and writes transcripts/notes back there
#   - everything is visible on both sides in real time
VOLUME ["/app/meetings"]

# ----- Default command --------------------------------------------------------
# When you run "docker compose run pipeline" with no extra arguments,
# this is what executes. You can override it by appending arguments:
#   docker compose run pipeline --file meetings/inbox/2026-04-07_sync.m4a
ENTRYPOINT ["python", "src/pipeline.py"]
