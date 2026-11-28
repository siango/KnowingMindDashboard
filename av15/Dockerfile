FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git openssl \
    && rm -rf /var/lib/apt/lists/*
COPY . /opt/kmcp-source
RUN pip install --no-cache-dir --no-deps /opt/kmcp-source \
    && useradd --create-home --uid 10001 kmcp
USER kmcp
WORKDIR /workspace
ENTRYPOINT ["kmcp"]
