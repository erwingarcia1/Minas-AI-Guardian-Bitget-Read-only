FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY sentinel/ sentinel/
COPY config/ config/
# Ops scripts that must run where /data/sentinel.db lives (#77). v1.4.1's
# release note promised the retention boundary reading could be taken on the
# deployment; without this line it could not. Deliberately per-file, not
# scripts/: the dev tooling (probes, hooks, release) has no business in a
# production image holding customer credentials.
COPY scripts/retention-boundary.py scripts/retention-boundary.py

# [triage] pulls the anthropic SDK so the documented `sentinel triage`
# command works when an API key is configured; harmless when it isn't.
RUN pip install --no-cache-dir ".[triage]"

# Non-root: the service holds customer credentials; least privilege applies
# to our own containers too.
RUN useradd --create-home sentinel && mkdir -p /data && chown sentinel:sentinel /data
USER sentinel

# What this image was built from. The build context has no `.git` (only
# sentinel/, config/, pyproject.toml and scripts/retention-boundary.py are
# copied), so the commit cannot be discovered at runtime and has to be
# passed in. scripts/deploy-gate.sh --export prints both; docker-compose.yml
# forwards them.
#
# Defaulting to `unknown` rather than to something plausible is the point: a
# build that misreports its commit is worse than one that admits it does not
# know, and `sentinel version` exits non-zero when it does not know.
ARG GIT_SHA=unknown
ARG GIT_TAG=unknown

ENV SENTINEL_DB_PATH=/data/sentinel.db \
    SENTINEL_RULES_PATH=/app/config/rules.yaml \
    SENTINEL_GIT_SHA=$GIT_SHA \
    SENTINEL_GIT_TAG=$GIT_TAG

EXPOSE 8390 8391 9109

CMD ["sentinel", "run"]
