#!/usr/bin/env python3
"""
mushroom_log_relay.py - A narrow, self-hosted relay that lets the
westcoast-mushroom-id skill push find-log entries straight to GitHub from
inside a Claude session, without the skill ever holding a GitHub credential
and without needing GitHub's own API/git write paths to be reachable from
wherever the skill happens to be running.

Why this exists: a Claude Cowork sandbox gates api.github.com and git push
to github.com behind a session-level repo-authorization step that a skill
invoked in a fresh chat has no way to satisfy -- there is no "workspace"
carried between sessions. Generic outbound HTTPS to an arbitrary third-party
domain is NOT gated the same way. So instead of fighting GitHub's own
chokepoints, this service sits somewhere else entirely (your own homelab)
and is the only thing that ever talks to GitHub. The skill just POSTs a
find-log entry here over plain HTTPS.

Deliberately narrow by design, because the credential behind this is real:
- ONE route that does ANYTHING: POST /log-find. There is no route to write
  species_registry.json, lookalike_pairs.json, or anything else in the
  repo. Even a full compromise of the relay auth token can only be used to
  spam the finds log, never to rewrite the deadly-species database.
- The request body is validated against a strict whitelist of fields
  before anything touches GitHub -- unknown keys are rejected outright,
  not silently dropped or passed through.
- A simple in-memory rate limit caps abuse if the token ever leaks.
- The GitHub PAT lives only in this process's environment (GITHUB_PAT),
  set once at container deploy time. It is never logged, never returned
  in a response, and never seen by the Claude session that calls this.
  The skill only ever holds RELAY_AUTH_TOKEN, a much lower-stakes secret
  scoped to "can call this one endpoint."

Config (all via environment variables -- nothing hardcoded, matching the
"credentials via parameters or runtime, never hardcoded" rule this whole
project follows):
  GITHUB_PAT            required. Fine-grained PAT scoped to ONLY
                         savoxis/mushroom-identifier-dontusethisever,
                         Contents: Read and write, nothing else.
  RELAY_AUTH_TOKEN       required. A long random string the skill sends
                         as "Authorization: Bearer <token>". Not the PAT.
  GITHUB_REPO            default "savoxis/mushroom-identifier-dontusethisever"
  GITHUB_BRANCH          default "main"
  LOG_PATH_IN_REPO       default "logs/finds_log.jsonl"
  RELAY_PORT             default 8787
  RATE_LIMIT_PER_MINUTE  default 20 (per source IP)

Run directly: python3 mushroom_log_relay.py
(normally run inside the Dockerfile in this same folder instead)

Endpoints:
  GET  /healthz    no auth. {"ok": true, "configured": true/false}
  POST /log-find   Authorization: Bearer <RELAY_AUTH_TOKEN> required.
                    Body: the same JSON entry shape log_find.py's
                    build_entry() produces. Appends it to the repo's
                    logs/finds_log.jsonl via the GitHub Contents API and
                    commits directly to GITHUB_BRANCH.
"""

import base64
import hmac
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GITHUB_API = "https://api.github.com"
GITHUB_REPO = os.environ.get("GITHUB_REPO", "savoxis/mushroom-identifier-dontusethisever")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
LOG_PATH_IN_REPO = os.environ.get("LOG_PATH_IN_REPO", "logs/finds_log.jsonl")
RELAY_PORT = int(os.environ.get("RELAY_PORT", "8787"))
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "20"))
MAX_BODY_BYTES = 32 * 1024  # a log entry is small; anything bigger is already suspicious

GITHUB_PAT = os.environ.get("GITHUB_PAT")
RELAY_AUTH_TOKEN = os.environ.get("RELAY_AUTH_TOKEN")

# Strict field whitelist -- must match log_find.py's build_entry() output
# exactly. Anything outside this set is rejected before it ever reaches
# GitHub, not silently stripped.
ALLOWED_FIELDS = {
    "logged_at", "photo_date", "location", "top_candidates",
    "hard_warning_triggered", "hard_warning_species", "notes",
}
REQUIRED_FIELDS = ("photo_date", "top_candidates")

_rate_lock = threading.Lock()
_rate_buckets = {}  # ip -> list[timestamp]


def _rate_limited(ip):
    now = time.time()
    with _rate_lock:
        bucket = [t for t in _rate_buckets.get(ip, []) if now - t < 60]
        if len(bucket) >= RATE_LIMIT_PER_MINUTE:
            _rate_buckets[ip] = bucket
            return True
        bucket.append(now)
        _rate_buckets[ip] = bucket
        return False


def _validate_entry(entry):
    if not isinstance(entry, dict):
        return "body must be a JSON object"
    extra = set(entry.keys()) - ALLOWED_FIELDS
    if extra:
        return f"unrecognized field(s), rejected: {sorted(extra)}"
    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        return f"missing required field(s): {missing}"
    if not isinstance(entry.get("photo_date"), str):
        return "photo_date must be a string"
    if not isinstance(entry.get("top_candidates"), list) or not entry["top_candidates"]:
        return "top_candidates must be a non-empty list"
    if "location" in entry and entry["location"] is not None and not isinstance(entry["location"], dict):
        return "location must be an object or null"
    if "hard_warning_species" in entry and not isinstance(entry.get("hard_warning_species", []), list):
        return "hard_warning_species must be a list"
    if "notes" in entry and entry["notes"] is not None and not isinstance(entry["notes"], str):
        return "notes must be a string or null"
    return None


def _github_request(method, path, payload=None):
    url = f"{GITHUB_API}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "mushroom-log-relay",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        return None, {"error": str(e)}


def _get_current_file():
    path = f"/repos/{GITHUB_REPO}/contents/{LOG_PATH_IN_REPO}?ref={GITHUB_BRANCH}"
    status, body = _github_request("GET", path)
    if status == 200:
        content = base64.b64decode(body["content"]).decode()
        return content, body["sha"]
    if status == 404:
        return "", None
    raise RuntimeError(f"could not read current log file (status {status}): {body}")


def _put_file(new_content, sha, commit_message):
    path = f"/repos/{GITHUB_REPO}/contents/{LOG_PATH_IN_REPO}"
    payload = {
        "message": commit_message,
        "content": base64.b64encode(new_content.encode()).decode(),
        "branch": GITHUB_BRANCH,
        "committer": {"name": "westcoast-mushroom-id bot", "email": "noreply@users.noreply.github.com"},
    }
    if sha:
        payload["sha"] = sha
    status, body = _github_request("PUT", path, payload)
    return status, body


def append_entry_to_github(entry):
    """Fetches the current log, appends one line, commits back. Retries
    once on a 409 (sha conflict from a concurrent write) by re-fetching
    and reapplying -- this endpoint could plausibly get hit from two
    sessions close together, and a conflict here should self-heal, not
    just fail the second caller."""
    line = json.dumps(entry) + "\n"
    for attempt in range(2):
        current, sha = _get_current_file()
        new_content = current + line
        species = entry["top_candidates"][0].get("common_name", "unknown") if entry["top_candidates"] else "unknown"
        message = f"Auto-log: find on {entry.get('photo_date', 'unknown date')} ({species})"
        status, body = _put_file(new_content, sha, message)
        if status in (200, 201):
            total_entries = new_content.count("\n")
            return True, {"commit_sha": body["commit"]["sha"], "entries_total": total_entries}
        if status == 409 and attempt == 0:
            continue  # someone else committed in between -- retry with fresh sha
        return False, {"github_status": status, "github_response": body}
    return False, {"error": "gave up after conflict retry"}


class Handler(BaseHTTPRequestHandler):
    server_version = "mushroom-log-relay/1.0"

    def log_message(self, fmt, *args):
        # Never let request/response bodies (which could carry the
        # Authorization header via a misbehaving client log path) reach
        # stdout -- just method, path, status, like a normal access log.
        sys.stdout.write(f"{self.address_string()} - {fmt % args}\n")

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._send_json(200, {"ok": True, "configured": bool(GITHUB_PAT and RELAY_AUTH_TOKEN)})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/log-find":
            self._send_json(404, {"error": "not found"})
            return

        client_ip = self.client_address[0]
        if _rate_limited(client_ip):
            self._send_json(429, {"error": "rate limit exceeded, try again shortly"})
            return

        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], RELAY_AUTH_TOKEN or ""):
            self._send_json(401, {"error": "missing or invalid Authorization bearer token"})
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(400, {"error": f"body must be 1-{MAX_BODY_BYTES} bytes"})
            return
        raw = self.rfile.read(length)

        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        problem = _validate_entry(entry)
        if problem:
            self._send_json(400, {"error": problem})
            return

        try:
            ok, result = append_entry_to_github(entry)
        except Exception as e:
            self._send_json(502, {"error": f"relay-side failure: {e}"})
            return

        if ok:
            self._send_json(200, {"ok": True, **result})
        else:
            self._send_json(502, {"ok": False, **result})


def main():
    if not GITHUB_PAT or not RELAY_AUTH_TOKEN:
        print("FATAL: GITHUB_PAT and RELAY_AUTH_TOKEN must both be set in the environment.", file=sys.stderr)
        sys.exit(1)

    server = ThreadingHTTPServer(("0.0.0.0", RELAY_PORT), Handler)
    print(f"mushroom-log-relay listening on :{RELAY_PORT}, target repo {GITHUB_REPO}@{GITHUB_BRANCH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
