# InferencePilot

An agent that drives traffic at your own inference server, routes each task to a cheap or
expensive model path, and adapts concurrency and output length when latency or errors creep up.
A live dashboard shows what it's doing and why.

## Setup

```
pip install -r requirements.txt
kubectl port-forward svc/vllm 8000:8000   # or point --base at wherever vLLM is reachable
python3 inferencepilot.py
```

Open http://localhost:8080.

Flags:
- `--base` — inference server base URL (default `http://localhost:8000`)
- `--port` — dashboard port (default `8080`)

## Using the dashboard

- **Run agent** — starts a batch of tasks with the given `tasks`, `start concurrency`, and
  `latency target p95 (ms)`. Uncheck **adaptive** to run the same batch at fixed concurrency
  with no adaptation, for comparison.
- **Ask the model** — submits one task directly and shows the route the agent chose (model vs.
  rule-based fallback), latency, output token budget, and the real answer.
- The latency/concurrency chart, live task feed, decision timeline, and run-comparison table
  update once per second while a run is in progress.

## How it works

- **Routing** — each task is classified `simple` or `complex` by a forced tool call to the
  model (`route_task`). If the model call fails or returns a bad label, a rule-based heuristic
  (`heuristic()` in `inferencepilot.py`) decides instead.
- **Retries** — transport-level hiccups (connection refused/reset against the port-forward) are
  retried up to `CONN_TRIES` times with a short wait; that wait is subtracted from reported
  latency so tunnel flakiness doesn't pollute the numbers. Each task also gets one retry if the
  model call itself raises.
- **Timing** — every call's wall-clock time is tracked; per-wave p50/p95 latency comes from the
  successful calls in that wave.
- **Adaptive decisions** — after each wave, the engine scrapes `/metrics` on the inference
  server for running/waiting request counts and compares wave p95 against the latency target:
  - errors or p95 over target → **BACKOFF** (halve concurrency, shrink the output token budget)
  - p95 comfortably under target → **RECOVER** (grow concurrency and budget back up)
  - otherwise → **HOLD**
  In fixed mode this logic is skipped entirely (**FIXED**, no adaptation). If every call in a
  wave fails, the run aborts rather than hammering a dead endpoint.
- **API endpoints**:
  - `GET /` — dashboard (`static/index.html`)
  - `GET /static/<file>` — CSS/JS assets
  - `GET /state` — current run state, history, and connection status, as JSON
  - `POST /start` — start a run (`total`, `start_conc`, `slo_ms`, `adaptive`)
  - `POST /ask` — run a single task (`text`) and return its routed answer
  - `POST /verify` — re-check the connection to the inference server with a real test call

## Files

```
inferencepilot.py   # HTTP server, routing/retry/adaptive engine, API endpoints
static/
  index.html         # dashboard layout
  styles.css         # dashboard appearance
  app.js             # buttons, polling, and the latency/concurrency chart
requirements.txt     # Python dependencies
```

Earlier iterations of `inferencepilot.py` are kept in `backups/`
(`inferencepilot_v2*.py`, `inferencepilot_v4*.py`, `inferencepilot_v5_backup.py`,
`inferencepilot_pre_static_refactor_backup.py`) as backups of prior working versions;
`inferencepilot.py` is the current one.
