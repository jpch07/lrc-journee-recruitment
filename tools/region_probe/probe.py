"""One-shot scheduled read-only exchange probe. No recruitment app imports."""
import json
import multiprocessing
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg
from psycopg.conninfo import conninfo_to_dict

SAMPLES = 20
SLOT_SECONDS = 60
CHILD_DEADLINE = 35
PRESSURE_RESERVE = 4
STATE = {"batches": [], "finished": False}
LOCK = threading.Lock()


def batch(queue):
    """Every session/transaction is read-only; synchronous, no pipelining."""
    result = {"warm_ms": [], "failures": 0, "closed": False}
    connection = None
    try:
        secret_root = Path(os.getenv("PROBE_SECRET_DIR", "/etc/secrets"))
        uri = json.loads((secret_root / "connection.json").read_text())["service_uri"]
        uri = uri.replace("postgresql+psycopg:", "postgresql:").replace("postgresql+psycopg2:", "postgresql:")
        source = conninfo_to_dict(uri)
        args = {key: source[key] for key in ("host", "port", "dbname", "user", "password") if key in source}
        args.update(sslmode="verify-full", sslrootcert=str(secret_root / "ca.pem"),
                    connect_timeout=5, application_name="lrc-region-readonly-probe",
                    options="-c default_transaction_read_only=on -c statement_timeout=1000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=5000")
        started = time.perf_counter()
        connection = psycopg.connect(**args, prepare_threshold=None)
        result["connection_ms"] = round((time.perf_counter() - started) * 1000, 3)
        connection.execute("BEGIN READ ONLY")
        read_only = connection.execute("SHOW transaction_read_only").fetchone()[0]
        tls = connection.execute("SELECT ssl,version FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        usage = connection.execute("SELECT count(*) FILTER (WHERE backend_type='client backend'), current_setting('max_connections')::int, current_setting('superuser_reserved_connections')::int, current_setting('reserved_connections')::int FROM pg_stat_activity").fetchone()
        capacity = min(20, usage[1] - usage[2] - usage[3])
        result.update(read_only=read_only == "on", tls_verified=bool(tls and tls[0]),
                      tls_version=tls[1] if tls else None, connections_including_probe=usage[0],
                      conservative_headroom=capacity - usage[0])
        if read_only != "on" or not tls or not tls[0] or capacity - usage[0] < PRESSURE_RESERVE:
            raise RuntimeError("safety gate")
        for _ in range(SAMPLES):
            started = time.perf_counter()
            if connection.execute("SELECT 1").fetchone() != (1,):
                raise RuntimeError("unexpected result")
            result["warm_ms"].append(round((time.perf_counter() - started) * 1000, 3))
        connection.rollback()
    except Exception as error:
        # Never render exception messages, connection parameters or raw SQL results.
        result.update(failures=1, error_type=type(error).__name__)
    finally:
        if connection is not None:
            connection.close()
        result["closed"] = True
    queue.put(result)


def schedule(start, region):
    context = multiprocessing.get_context("spawn")
    offset = 0 if region == "virginia" else 1
    for index in range(3):
        due = start + (offset + index * 2) * SLOT_SECONDS
        delay = due - time.time()
        if delay > 0:
            time.sleep(delay)
        # Skip missed slots; a restart never catches up or retries a batch.
        if abs(time.time() - due) > 2 or time.time() >= start + 360:
            with LOCK:
                STATE["batches"].append({"index": index, "failures": 1, "error_type": "MissedSlot", "closed": True})
            break
        queue = context.Queue()
        worker = context.Process(target=batch, args=(queue,))
        worker.start()
        worker.join(CHILD_DEADLINE)
        if worker.is_alive():
            worker.kill()
            worker.join(3)
            result = {"failures": 1, "error_type": "Deadline", "closed": not worker.is_alive()}
        else:
            try:
                result = queue.get(timeout=1)
            except Exception:
                result = {"failures": 1, "error_type": "WorkerFailure", "closed": True}
        queue.close()
        queue.join_thread()
        result.update(index=index, scheduled_utc=due, completed_utc=time.time())
        with LOCK:
            STATE["batches"].append(result)
        print(json.dumps({"region": region, "batch": result}), flush=True)
        if result.get("failures"):
            break
    with LOCK:
        STATE["finished"] = True


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/health", "/results"):
            self.send_error(404)
            return
        with LOCK:
            value = {"region": os.environ["PROBE_REGION"], "utc": time.time(),
                     "start_utc": int(os.environ["PROBE_START_UTC"]), **STATE}
            body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    start = int(os.environ["PROBE_START_UTC"])
    region = os.environ["PROBE_REGION"]
    if region not in ("virginia", "oregon") or start < time.time() or start > time.time() + 900:
        raise SystemExit("Invalid probe schedule")
    threading.Thread(target=schedule, args=(start, region), daemon=True).start()
    HTTPServer(("0.0.0.0", int(os.getenv("PORT", "10000"))), Handler).serve_forever()
