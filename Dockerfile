# =====================================================================
# STAGE 1: COMPILATION ENGINE & DEPENDENCY BUILDER
# =====================================================================
FROM python:3.11-slim AS system-compiler

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /matrix_build

# Install native extension compilation toolchains
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Cache dependency layer
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/matrix_dependencies -r requirements.txt


# =====================================================================
# STAGE 2: HIGH-SECURITY RUNTIME PROPRIETARY ENVIRONMENT
# =====================================================================
FROM python:3.11-slim AS runtime-engine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHON_CORE_VERSION=4.02

WORKDIR /platform

# Install only runtime database libraries (no compiler toolchains)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Pull compiled Python dependencies from Stage 1
COPY --from=system-compiler /matrix_dependencies /usr/local

# Copy the core source into the deployment frame
COPY . .

# Hardened System Control: run as unprivileged operator
RUN useradd --create-home --shell /bin/bash matrix_operator && \
    chown -R matrix_operator:matrix_operator /platform

USER matrix_operator

# Expose target internal port for the Nginx gateway
EXPOSE 8000

# Flask core served by Gunicorn with eventlet for flask-sock WebSockets
CMD ["gunicorn", "-k", "eventlet", "-w", "1", "-b", "0.0.0.0:8000", "app:app"]
