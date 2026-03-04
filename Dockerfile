# Dockerfile for Cloud Run deployment
# Uses pixi multi-stage build following official documentation
# https://pixi.prefix.dev/latest/deployment/container/

FROM ghcr.io/prefix-dev/pixi:0.41.4 AS build

WORKDIR /app

# Copy pixi files first for better caching
COPY pixi.toml pixi.lock ./

# Install production dependencies
RUN pixi install --locked -e prod

# Create the shell-hook bash script to activate the environment
RUN pixi shell-hook -e prod -s bash > /shell-hook
RUN echo "#!/bin/bash" > /app/entrypoint.sh
RUN cat /shell-hook >> /app/entrypoint.sh
RUN echo 'exec "$@"' >> /app/entrypoint.sh

# Production stage
FROM ubuntu:24.04 AS production

WORKDIR /app

# Copy the production environment (path must match build stage)
COPY --from=build /app/.pixi/envs/prod /app/.pixi/envs/prod
COPY --from=build /app/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Copy application code
COPY . ./

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Cloud Run sets PORT environment variable
ENV PORT=8080
EXPOSE 8080

ENTRYPOINT [ "/app/entrypoint.sh" ]
# Use hypercorn for HTTP/2 support (required for Cloud Run >32 MiB responses)
CMD [ "hypercorn", "main:app", "--bind", "0.0.0.0:8080" ]
