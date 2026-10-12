#!/usr/bin/env python3
"""InferencePilot: an agent that watches its own inference server and adapts.

Run:  python inferencepilot_app.py            (needs: pip install openai requests)
Open: http://localhost:8080
Needs the port-forward running:  kubectl port-forward svc/vllm 8000:8000
"""
import argparse, json, os, re, threading, time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
import openai
from openai import OpenAI

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript"}

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8000")   # your vLLM (via port-forward)
ap.add_argument("--port", type=int, default=8080)            # dashboard port
args = ap.parse_args()
BASE = args.base.rstrip("/")

client = OpenAI(base_url=BASE + "/v1", api_key="not-needed", timeout=120, max_retries=0)
try:
    MODEL = requests.get(BASE + "/v1/models", timeout=10).json()["data"][0]["id"]
except Exception:
    MODEL = "unknown"
EXTRA = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}  # no <think> block = faster
print("model:", MODEL, "| endpoint:", BASE)
CONN = {"ok": False, "model": None, "err": "not verified yet", "ms": None}

def verify():
    """Only report connected after a real successful completion."""
    t0 = time.perf_counter()
    try:
        mid = requests.get(BASE + "/v1/models", timeout=10).json()["data"][0]["id"]
        client.chat.completions.create(model=mid, max_tokens=8, temperature=0, **EXTRA,
                                       messages=[{"role": "user", "content": "Say OK."}])
        CONN.update(ok=True, model=mid, err=None, ms=round((time.perf_counter() - t0) * 1000))
    except Exception as e:
        CONN.update(ok=False, model=None, err=str(e)[:140], ms=None)

LOCK = threading.Lock()
S = {}
HISTORY = []   # summaries of past runs, kept across runs for the comparison table

def reset_state():
    S.clear()
    S.update({"status": "idle", "model": MODEL, "endpoint": BASE, "events": [], "recent": [],
              "calls": 0, "call_ms_sum": 0.0, "done": 0, "ok": 0, "total": 0,
              "route_model": 0, "route_fallback": 0, "wall_s": 0, "config": {}, "log": [], "last_error": None, "aborted": False, "conn_retries": 0})
reset_state()

def log(msg):
    with LOCK:
        S["log"] = (S["log"] + [{"t": time.strftime("%H:%M:%S"), "msg": msg}])[-120:]

ROUTE_TOOL = [{"type": "function", "function": {
    "name": "route_task", "description": "Decide how much effort this task needs.",
    "parameters": {"type": "object", "properties": {
        "complexity": {"type": "string", "enum": ["simple", "complex"]}}, "required": ["complexity"]}}}]

TASKS = [
    "Classify this log line as INFO, WARN or ERROR: 'db connection pool exhausted'",
    "Summarize in one line: the checkout service restarted twice after a memory spike.",
    "Why does decode get slower as the KV cache fills up? Explain the root cause and tradeoffs.",
    "Analyze this failure: p95 latency tripled after traffic doubled, GPU memory is 91% full, requests queue. Give causes and fixes.",
    "Classify this log line as INFO, WARN or ERROR: 'health check ok'",
    "Compare serving a 4B model in FP8 versus BF16 for an agent workload. Recommend one with reasons.",
    "What status code means rate limited?",
    "Design a retry policy for an agent that calls an inference endpoint that sometimes times out. Explain each choice.",
]

TL = threading.local()
CONN_TRIES, CONN_WAIT = 8, 1.5

def chat(**kw):
    """One model call. If the tunnel flaps (connection refused/reset), wait and retry; the waiting is not counted as latency."""
    for k in range(CONN_TRIES):
        t0 = time.perf_counter()
        try:
            r = client.chat.completions.create(model=MODEL, temperature=0, **EXTRA, **kw)
            break
        except openai.APIConnectionError as e:
            if isinstance(e, openai.APITimeoutError) or k == CONN_TRIES - 1:
                raise
            TL.pen = getattr(TL, "pen", 0.0) + CONN_WAIT * 1000
            with LOCK:
                S["conn_retries"] = S.get("conn_retries", 0) + 1
            time.sleep(CONN_WAIT)
    ms = (time.perf_counter() - t0) * 1000
    with LOCK:
        S["calls"] += 1
        S["call_ms_sum"] += ms
    return r

def heuristic(task):
    t = task.lower()
    return "complex" if len(task) > 90 or any(w in t for w in ("why", "analyze", "compare", "design", "root cause")) else "simple"

def route(task):
    """The model picks simple/complex through a forced tool call. Bad output -> rule-based fallback."""
    try:
        r = chat(max_tokens=40, tools=ROUTE_TOOL,
                 tool_choice={"type": "function", "function": {"name": "route_task"}},
                 messages=[{"role": "system", "content": "Route the task. simple = short lookup or classification. complex = needs reasoning or analysis."},
                           {"role": "user", "content": task}])
        label = json.loads(r.choices[0].message.tool_calls[0].function.arguments)["complexity"]
        if label in ("simple", "complex"):
            return label, "model"
    except Exception as e:
        with LOCK:
            S["last_error"] = str(e)[:120]
    return heuristic(task), "fallback"

def work(task, label, deep_tokens):
    if label == "simple":
        system, mt = "Answer in one short sentence.", 60
    else:
        system, mt = "Reason briefly, then give a clear structured answer.", deep_tokens
    chat(max_tokens=mt, messages=[{"role": "system", "content": system}, {"role": "user", "content": task}])

def one(task, deep_tokens):
    TL.pen = 0.0
    t0 = time.perf_counter()
    label, src = route(task)
    err = None
    for _ in range(2):                       # one retry
        try:
            work(task, label, deep_tokens)
            err = None
            break
        except Exception as e:
            err = str(e)[:80]
            with LOCK:
                S["last_error"] = err
            time.sleep(0.5)
    ms = max(0, round((time.perf_counter() - t0) * 1000 - TL.pen))
    r = {"task": task, "label": label, "src": src, "ms": ms, "ok": err is None}
    with LOCK:
        S["done"] += 1
        S["ok"] += 1 if r["ok"] else 0
        S["route_model" if src == "model" else "route_fallback"] += 1
        S["recent"] = ([r] + S["recent"])[:14]
    return r

def ask(text):
    text = (text or "").strip()[:2000]
    if not text:
        return {"ok": False, "error": "empty task"}
    t0 = time.perf_counter()
    label, src = route(text)
    if label == "simple":
        system, mt = "Answer in one short sentence.", 60
    else:
        system, mt = "Reason briefly, then give a clear structured answer.", 300
    try:
        r = chat(max_tokens=mt, messages=[{"role": "system", "content": system}, {"role": "user", "content": text}])
        u = getattr(r, "usage", None)
        return {"ok": True, "label": label, "route_src": src, "max_tokens": mt,
                "ms": round((time.perf_counter() - t0) * 1000), "text": r.choices[0].message.content,
                "prompt_tokens": getattr(u, "prompt_tokens", None), "completion_tokens": getattr(u, "completion_tokens", None)}
    except Exception as e:
        return {"ok": False, "label": label, "error": str(e)[:160]}

def scrape():
    out = {"running": None, "waiting": None}
    try:
        for line in requests.get(BASE + "/metrics", timeout=3).text.splitlines():
            m = re.match(r"vllm:num_requests_(running|waiting)\{[^}]*\}\s+([0-9.eE+-]+)", line)
            if m:
                out[m.group(1)] = float(m.group(2))
    except Exception:
        pass
    return out

def pct(vals, p):
    vals = sorted(vals)
    return vals[int(p * (len(vals) - 1))] if vals else 0

def engine(total, start_conc, slo_ms, adaptive):
    min_conc, deep_tokens = 4, 300
    conc, i, wave = start_conc, 0, 0
    tasks = [TASKS[k % len(TASKS)] for k in range(total)]
    allms, backoffs = [], 0
    log(f"run started: {total} tasks, start concurrency {start_conc}, target p95 {slo_ms} ms, mode {'adaptive' if adaptive else 'fixed (no adaptation)'}")
    t_start = time.perf_counter()
    try:
        while i < total:
            wave += 1
            batch = tasks[i:i + conc]
            peak = {"running": 0, "waiting": 0}
            stop = threading.Event()
            def sampler():
                while not stop.is_set():
                    m = scrape()
                    for k in ("running", "waiting"):
                        if m[k] is not None:
                            peak[k] = max(peak[k], m[k])
                    stop.wait(0.4)
            threading.Thread(target=sampler, daemon=True).start()
            with ThreadPoolExecutor(max_workers=len(batch)) as ex:
                res = list(ex.map(lambda t: one(t, deep_tokens), batch))
            stop.set()
            i += len(batch)
            lat = [r["ms"] for r in res if r["ok"]]
            allms += lat
            errs = sum(1 for r in res if not r["ok"])
            p50, p95 = pct(lat, 0.5), pct(lat, 0.95)
            if not adaptive:
                action, nc, nt = "FIXED", conc, deep_tokens
            elif errs > 0 or p95 > slo_ms:
                action, nc, nt = "BACKOFF", max(min_conc, conc // 2), max(100, int(deep_tokens * 0.6))
                backoffs += 1
            elif p95 < slo_ms * 0.7 and conc < start_conc:
                action, nc, nt = "RECOVER", min(start_conc, conc + 8), min(300, deep_tokens + 60)
            else:
                action, nc, nt = "HOLD", conc, deep_tokens
            ev = {"wave": wave, "conc": conc, "n": len(batch), "p50": round(p50), "p95": round(p95),
                  "errors": errs, "action": action, "next_conc": nc, "deep_tokens": nt,
                  "running": peak["running"], "waiting": peak["waiting"]}
            with LOCK:
                S["events"].append(ev)
                S["wall_s"] = round(time.perf_counter() - t_start, 1)
            if action == "BACKOFF":
                why = f"p95 {round(p95)} ms over target {slo_ms} ms" + (f" and {errs} errors" if errs else "")
                log(f"wave {wave}: {why} -> concurrency {conc}->{nc}, deep output budget {deep_tokens}->{nt} tokens")
            elif action == "RECOVER":
                log(f"wave {wave}: p95 {round(p95)} ms under 70% of target -> concurrency {conc}->{nc}, deep budget {deep_tokens}->{nt}")
            elif action == "HOLD":
                log(f"wave {wave}: p95 {round(p95)} ms within target, holding concurrency {conc}")
            else:
                log(f"wave {wave}: p95 {round(p95)} ms (fixed mode, no adaptation)")
            if errs == len(batch):
                with LOCK:
                    S["aborted"] = True
                    why_err = S.get("last_error")
                log(f"ENDPOINT DOWN: all {errs} calls in wave {wave} failed ({why_err}). Stopping run. Check the kubectl port-forward terminal, then press Verify/Run again.")
                break
            conc, deep_tokens = nc, nt
    finally:
        if S.get("conn_retries"):
            log(f"note: {S['conn_retries']} connection retries absorbed (tunnel hiccups); the waiting is not counted as model latency")
        log("run finished")
        wall = time.perf_counter() - t_start
        with LOCK:
            S["status"] = "done"
            S["wall_s"] = round(wall, 1)
            if not S.get("aborted"):
              HISTORY.append({"mode": "adaptive" if adaptive else "fixed", "start_conc": start_conc,
                            "slo_ms": slo_ms, "tasks": S["ok"], "wall_s": round(wall, 1),
                            "tps": round(S["ok"] / wall, 1) if wall else 0,
                            "p50": round(pct(allms, 0.5)), "p95": round(pct(allms, 0.95)),
                            "over_slo": round(100 * sum(1 for x in allms if x > slo_ms) / max(1, len(allms))),
                            "backoffs": backoffs})
            del HISTORY[:-6]

def start(cfg):
    with LOCK:
        if S["status"] == "running":
            return False
    total = max(8, min(600, int(cfg.get("total", 150))))
    sc = max(4, min(128, int(cfg.get("start_conc", 48))))
    slo = max(300, int(cfg.get("slo_ms", 6000)))
    adaptive = bool(cfg.get("adaptive", True))
    reset_state()
    S["status"], S["total"] = "running", total
    S["config"] = {"total": total, "start_conc": sc, "slo_ms": slo, "adaptive": adaptive}
    threading.Thread(target=engine, args=(total, sc, slo, adaptive), daemon=True).start()
    return True

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _send_file(self, rel_path):
        path = os.path.normpath(os.path.join(STATIC_DIR, rel_path))
        if not (path == STATIC_DIR or path.startswith(STATIC_DIR + os.sep)) or not os.path.isfile(path):
            self._send(404, "not found", "text/plain")
            return
        with open(path, "rb") as f:
            self._send(200, f.read(), MIME.get(os.path.splitext(path)[1], "application/octet-stream"))
    def do_GET(self):
        if self.path == "/state":
            with LOCK:
                self._send(200, json.dumps({**S, "history": HISTORY, "conn": CONN}), "application/json")
        elif self.path == "/" or self.path == "/index.html":
            self._send_file("index.html")
        elif self.path.startswith("/static/"):
            self._send_file(self.path[len("/static/"):])
        else:
            self._send(404, "not found", "text/plain")
    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        try:
            cfg = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            cfg = {}
        if self.path == "/ask":
            self._send(200, json.dumps(ask(cfg.get("text", ""))), "application/json")
        elif self.path == "/verify":
            verify()
            self._send(200, json.dumps(CONN), "application/json")
        else:
            self._send(200, json.dumps({"started": start(cfg)}), "application/json")

if __name__ == "__main__":
    verify()
    print("connection:", "VERIFIED" if CONN["ok"] else "NOT CONNECTED - " + str(CONN["err"]))
    print(f"dashboard: http://localhost:{args.port}")
    ThreadingHTTPServer(("0.0.0.0", args.port), H).serve_forever()