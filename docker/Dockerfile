FROM python:3.12 AS builder

ARG NODENORM_REPO=https://github.com/biothings/NodeNormalizationAPI.git
ARG NODENORM_BRANCH=main

WORKDIR /build/nodenorm
RUN git clone -b ${NODENORM_BRANCH} --recursive ${NODENORM_REPO} .
RUN git rev-parse HEAD > /build/nodenorm/version.txt
RUN pip wheel --wheel-dir=/build/wheels /build/nodenorm

FROM caddy:2.8-builder AS caddy_builder
RUN xcaddy build

FROM python:3.12-slim
RUN apt update -q -y && apt install -y --no-install-recommends git supervisor curl telnet && rm -rf /var/lib/apt/lists/*
RUN useradd -m nodenorm && usermod -aG sudo nodenorm
USER nodenorm
WORKDIR /home/nodenorm/
# Set PyStow home directory to a writable location
ENV PYSTOW_HOME=/home/nodenorm/.pystow
RUN mkdir -p /home/nodenorm/.pystow
RUN python -m venv /home/nodenorm/venv
COPY --from=builder --chown=nodenorm:nodenorm /build/wheels /home/nodenorm/wheels
RUN /home/nodenorm/venv/bin/pip install /home/nodenorm/wheels/*.whl && rm -rf /home/nodenorm/wheels

# Apply patches to fix NodeNormalization compatibility issues
COPY --chown=nodenorm:nodenorm patches/health.py /home/nodenorm/venv/lib/python3.12/site-packages/nodenorm/handlers/health.py

WORKDIR /home/nodenorm/configuration
COPY --from=builder --chown=nodenorm:nodenorm /build/nodenorm/src/nodenorm/config/config.default.json /home/nodenorm/configuration/default.json
COPY --from=builder --chown=nodenorm:nodenorm /build/nodenorm/version.txt /home/nodenorm/configuration/version.txt

# Override with corrected configuration
COPY --chown=nodenorm:nodenorm configuration/config.json /home/nodenorm/configuration/config.json

# Switch back to root for system-level operations
USER root
COPY configuration/supervisord.conf /etc/supervisor/supervisord.conf
COPY configuration/Caddyfile /etc/caddy/Caddyfile
COPY --from=caddy_builder /usr/bin/caddy /usr/bin/caddy
# Switch back to nodenorm user for runtime
USER nodenorm


STOPSIGNAL SIGINT
EXPOSE 8000

# Run supervisord as root to manage processes 
USER root
ENTRYPOINT ["supervisord"]
CMD ["-c", "/etc/supervisor/supervisord.conf"]
