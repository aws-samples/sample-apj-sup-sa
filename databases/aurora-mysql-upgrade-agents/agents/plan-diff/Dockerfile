FROM python:3.12-slim

WORKDIR /app

# RDS/Aurora global CA bundle for TLS-verified DB connections (ssl_verify_cert).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSL -o /app/rds-ca-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
 && apt-get purge -y curl && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

EXPOSE 8080
ENV PYTHONUNBUFFERED=1

CMD ["opentelemetry-instrument", "python", "agent.py"]
