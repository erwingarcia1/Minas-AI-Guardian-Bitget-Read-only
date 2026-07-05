FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY sentinel/ sentinel/
COPY config/ config/

# [triage] pulls the anthropic SDK so the documented `sentinel triage`
# command works when an API key is configured; harmless when it isn't.
RUN pip install --no-cache-dir ".[triage]"

# Non-root: the service holds customer credentials; least privilege applies
# to our own containers too.
RUN useradd --create-home sentinel && mkdir -p /data && chown sentinel:sentinel /data
USER sentinel

ENV SENTINEL_DB_PATH=/data/sentinel.db \
    SENTINEL_RULES_PATH=/app/config/rules.yaml

EXPOSE 8390 9109

CMD ["sentinel", "run"]
