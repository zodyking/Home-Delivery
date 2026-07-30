# Build arg must be declared before first FROM for multi-stage compatibility
ARG BUILD_FROM=ghcr.io/hassio-addons/debian-base:9.2.0

# Stage 1: Addon image with Python and Playwright
FROM ${BUILD_FROM}

# Install Python, Playwright deps, and Tesseract for letter OCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python service and install dependencies
COPY python-service/requirements.txt /app/python-service/
RUN pip3 install --break-system-packages --no-cache-dir -r /app/python-service/requirements.txt

# Install Playwright Chromium and system dependencies
RUN python3 -m playwright install chromium && python3 -m playwright install-deps chromium

# Copy full Python service
COPY python-service/ /app/python-service/

# Copy Home Assistant integration
COPY integration/ /app/integration/

# Copy frontend (vanilla JS panel)
COPY frontend/ /app/frontend/

# Copy root filesystem (S6 overlay)
COPY rootfs /
RUN chmod +x /etc/s6-overlay/s6-rc.d/init-home-delivery/run \
    /etc/s6-overlay/s6-rc.d/api/run /etc/s6-overlay/s6-rc.d/api/finish

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="Home Delivery" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="Home Delivery" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
