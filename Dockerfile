FROM python:3.12-slim

# snmpwalk / snmpget (net-snmp). Le paquet « snmp » ne contient pas les MIB propriétaires : inutile ici, les OID sont numériques.
RUN apt-get update \
    && apt-get install -y --no-install-recommends snmp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY printwatch ./printwatch
RUN pip install --no-cache-dir ".[web]" \
    && useradd --system --uid 10001 --home-dir /app printwatch \
    && mkdir -p /app/data && chown printwatch /app/data

USER printwatch
VOLUME ["/app/data"]
ENTRYPOINT ["printwatch"]
CMD ["--config", "/app/config.toml", "loop", "--printers", "/app/printers.csv"]
