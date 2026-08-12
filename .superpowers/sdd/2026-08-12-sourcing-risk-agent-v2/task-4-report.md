# Task 4 Report: Persistent Agent Graph Checkpoints

## Delivered

- Added `app.graphs.sourcing_risk_v2.checkpointer` with an independently managed psycopg3 `AsyncPostgresSaver` lifecycle.
- Builds the PostgreSQL URI from the configured PG fields, URL-encoding username, password, and database name; checkpoint tables use the configurable `agent_checkpoint` schema.
- `get_sourcing_risk_checkpointer()` creates the schema, runs saver setup once, and reuses the initialized saver. Setup failure closes the dedicated context and leaves no cached healthy saver.
- `close_sourcing_risk_checkpointer()` releases the saver context during application shutdown.
- Added `AGENT_RUN_V2_ENABLED` (default `false`) and `AGENT_RUN_CHECKPOINT_SCHEMA` settings. When enabled, lifespan setup fails explicitly if checkpoint initialization fails; readiness reports an unavailable `sourcing_risk_checkpointer` check until the saver is initialized.
- Added `langgraph-checkpoint-postgres`, psycopg3 binary, and pytest-asyncio dependency declarations.

## Verification

- RED: the lifecycle test first failed because `app.graphs.sourcing_risk_v2` did not exist.
- GREEN: `pytest backend/tests/test_agent_run_checkpointer.py backend/tests/test_health.py -v` — 11 passed.
- `python -m compileall -q backend/app/graphs/sourcing_risk_v2 backend/app/core/config.py backend/app/api/health.py backend/app/main.py` — passed.
- `git diff --check` — passed.

## Environment limitation

The active interpreter does not currently have `langgraph-checkpoint-postgres` (and therefore its psycopg3 saver implementation) installed, and no PostgreSQL integration environment was available. The new module deliberately gives an explicit startup error if V2 is enabled before those declared dependencies are installed. The lifecycle behavior is covered through mockable contracts; production startup should be smoke-tested after installing `backend/requirements.txt` against PostgreSQL.
