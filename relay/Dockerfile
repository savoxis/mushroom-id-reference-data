# Stdlib-only Python service -- no requirements.txt, nothing to pip install,
# smallest reasonable attack surface for something holding a real GitHub
# credential. Alpine base keeps the image small; swap to python:3.12-slim
# if Alpine's musl libc ever causes trouble (it shouldn't -- this script
# uses nothing beyond the standard library).
FROM python:3.12-alpine

# Run as a non-root user -- this process never needs to touch the
# filesystem beyond its own script, so it doesn't need root.
RUN adduser -D -u 1000 relay
USER relay

WORKDIR /app
COPY --chown=relay:relay mushroom_log_relay.py .

EXPOSE 8787

CMD ["python3", "mushroom_log_relay.py"]
