# Build the wheel with tools/release/verify_artifacts.py first. The image never
# installs from the checkout and never needs a compiler, display, GPU or daemon.
FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 AS base

COPY dist/release/artifacts/*.whl /tmp/
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin botbowl
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /home/botbowl

FROM base AS web
RUN wheel="$(find /tmp -maxdepth 1 -name 'botbowl-*.whl' -print -quit)" \
    && test -n "$wheel" \
    && python -m pip install --no-cache-dir "${wheel}[web]" \
    && rm "$wheel"
USER 10001:10001
EXPOSE 5000
CMD ["python", "-m", "botbowl", "web", "--host", "0.0.0.0", "--port", "5000"]

FROM base AS headless
RUN wheel="$(find /tmp -maxdepth 1 -name 'botbowl-*.whl' -print -quit)" \
    && test -n "$wheel" \
    && python -m pip install --no-cache-dir "$wheel" \
    && rm "$wheel"
USER 10001:10001
CMD ["python", "-m", "botbowl", "smoke", "--max-steps", "100"]
