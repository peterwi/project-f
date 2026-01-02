FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /work

# System deps needed for common scientific wheels and LightGBM runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential \
      git \
      curl \
      ca-certificates \
      libgomp1 && \
    rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip wheel setuptools

# Vendor Qlib into the image for reproducibility (repo provides ./qlib).
COPY qlib /opt/qlib-src
RUN python -m pip install -e /opt/qlib-src

# Writable working dir even when running as a host UID/GID via compose.
RUN mkdir -p /work && chmod 0777 /work

