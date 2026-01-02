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

# Constrain dependencies for Qlib compatibility.
COPY docker/qlib-runner/constraints.txt /opt/constraints.txt

# Vendor Qlib into the image for reproducibility (repo provides ./qlib).
COPY qlib /opt/qlib-src
# Qlib uses setuptools-scm for versioning; our vendored copy intentionally does not
# include git metadata. Provide a deterministic placeholder version at build time.
ARG QLIB_VERSION=0.0.0+vendored
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYQLIB=${QLIB_VERSION}
RUN python -m pip install -c /opt/constraints.txt /opt/qlib-src

# Writable working dir even when running as a host UID/GID via compose.
RUN mkdir -p /work && chmod 0777 /work
