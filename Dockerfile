# Dockerfile for GMUNVIS Mech Royale Submission
# Target platform: linux/amd64
# Runtime sandbox: 1 CPU, 512 MB RAM, read-only root, uid 65534, /tmp tmpfs 64MB

FROM python:3.13-slim

WORKDIR /bot

# Copy required bot files and protocol wrapper into the container
COPY mechfight.py .
COPY bot.py .

# Ensure permissions allow execution by unprivileged uid 65534 (nobody)
RUN chmod -R 755 /bot

# Unbuffered stdout is mandatory for protocol lockstep
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
