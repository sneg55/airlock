# Airlock Python app image. One image runs any component: the checkpoint service, the
# read-only agent, or the write-only executor daemon, selected by the `airlock` subcommand.
# Keep them in separate containers with separate credentials so the capability split is a
# deployment boundary (see docker-compose.yml).
FROM python:3.13-slim

WORKDIR /app
RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv pip install --system .

# Default to the checkpoint; compose overrides `command` per service.
ENTRYPOINT ["airlock"]
CMD ["checkpoint", "--host", "0.0.0.0", "--port", "8000"]
