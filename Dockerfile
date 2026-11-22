# Build stage (pinned rust version for reproducible linux/amd64 finalist rebuilds)
FROM rust:1.88-slim-bookworm AS builder
WORKDIR /src

# Cache dependencies first
COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src && echo 'fn main() {}' > src/main.rs && cargo build --release --locked && rm -rf src

# Build actual bot
COPY src ./src
RUN touch src/main.rs && cargo build --release --locked

# Minimal runtime image (uid 65534 compatible, no network, read-only FS)
FROM debian:bookworm-slim
COPY --from=builder /src/target/release/mech-royale /usr/local/bin/mech-royale
RUN chmod 0755 /usr/local/bin/mech-royale
ENTRYPOINT ["/usr/local/bin/mech-royale"]
