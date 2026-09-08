#!/usr/bin/env python3
"""Ananta client build monitor (client only).

Table-driven poller: ENDPOINTS declares what to fetch, how to parse it,
and where to snapshot it. The runner is generic; adding a new endpoint
means adding one table row.

Only small metadata files are fetched -- never multi-GB blobs.
No secrets or VPN needed (all endpoints verified directly reachable).
"""

import hashlib
import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "UnityPlayer/2022.3.56f1 (PC; Windows)", "Accept": "*/*"}
TIMEOUT = 20
MAX_BYTES = 8 * 1024 * 1024
CTX = ssl.create_default_context()

GPH_HOST = "https://l50.gph.netease.com"
GDL_HOST = "https://l50.gdl.netease.com"

ENDPOINTS = [
    {
        "id": "online",
        "url": "https://l50.update.netease.com/Online/pc_netease_version.json",
        "kind": "resver",
        "snapshot_dir": "snapshots/online",
    },
    {
        "id": "outertest1",
        "url": "https://serverlist-test.l50.leihuo.netease.com/OuterTest1/pc_netease_version.json",
        "kind": "resver",
        "snapshot_dir": "snapshots/outertest1",
    },
    {
        "id": "startup",
        "url": "https://l50.update.netease.com/Online/trunk-client_startup_patch_info.txt",
        "kind": "startup",
    },
]


# ---------- io ----------

def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
        body = r.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError(f"response too large: {url}")
        return {
            "body": body,
            "status": r.status,
            "etag": r.headers.get("ETag"),
            "sha256": hashlib.sha256(body).hexdigest(),
        }


def save(rel, content):
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return rel


def load_state():
    p = os.path.join(ROOT, "state.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def log_change(line):
    with open(os.path.join(ROOT, "changes.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def check_url(url):
    """Tiny reachability probe (HEAD, fallback to 1-byte Range GET).

    Returns (status_or_error_string, reachable_bool). Never downloads bodies.
    """
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            return r.status, r.status in (200, 206)
    except urllib.error.HTTPError as e:
        if e.code in (403, 405):
            try:  # some edges refuse HEAD; retry with 1-byte range
                req = urllib.request.Request(
                    url, headers={**UA, "Range": "bytes=0-0"})
                with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                    r.read(4)
                    return r.status, r.status in (200, 206)
            except urllib.error.HTTPError as e2:
                return e2.code, False
            except Exception as e2:
                return f"{type(e2).__name__}", False
        return e.code, False
    except Exception as e:
        return f"{type(e).__name__}", False


def notify(text):
    print(text, flush=True)
    hook = os.environ.get("DISCORD_WEBHOOK")
    if hook:
        try:
            req = urllib.request.Request(
                hook,
                data=json.dumps({"content": text[:1900]}).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=15, context=CTX).read(64)
        except Exception as e:
            print(f"webhook failed: {e}", flush=True)


# ---------- parsers ----------

def parse_startup(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        pid, rest = line.split("=", 1)
        files = {}
        for item in rest.split(","):
            item = item.strip()
            if ":" in item:
                name, md5 = item.rsplit(":", 1)
                files[name.strip()] = md5.strip()
            elif item:
                files[item] = ""
        out[pid.strip()] = files
    return out


# ---------- handlers (one per kind) ----------

def handle_resver(ep, res, prev, baseline, changes):
    data = json.loads(res["body"].decode("utf-8"))
    entries = data.get("resUpdate", [])
    cur = entries[0] if entries else {}
    resv = cur.get("resV", "")
    if resv and resv != prev.get(ep["id"], {}).get("resV"):
        save(f"{ep['snapshot_dir']}/{resv}/pc_netease_version.json", res["body"])
        if not baseline:
            changes.append(f"new {ep['id']} resV {resv} "
                           f"(codeV {cur.get('codeV')}, artifactV {cur.get('artifactV')})")
    return {ep["id"]: {
        "resV": resv, "resC": cur.get("resC", ""),
        "codeV": str(cur.get("codeV", "")), "artifactV": str(cur.get("artifactV", "")),
        "sha256": res["sha256"],
    }}


def handle_startup(ep, res, prev, baseline, changes, state):
    parsed = parse_startup(res["body"].decode("utf-8", errors="replace"))
    pids = sorted(parsed)
    if pids != prev.get("startup", {}).get("patch_ids", []):
        resv = state.get("online", {}).get("resV", "unknown")
        save(f"snapshots/online/{resv}/trunk-client_startup_patch_info.txt", res["body"])
        if not baseline:
            changes.append(f"startup manifest changed: {pids}")
    return {"startup": {"patch_ids": pids, "files": parsed, "sha256": res["sha256"]}}


HANDLERS = {"resver": handle_resver, "startup": handle_startup}


# ---------- runner ----------

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prev = load_state()
    baseline = not prev
    state = dict(prev)
    state["checked_at"] = now
    changes, errors = [], []

    for ep in ENDPOINTS:
        try:
            res = fetch(ep["url"])
        except Exception as e:
            errors.append(f"{ep['id']}: {type(e).__name__}: {e}")
            continue
        try:
            if ep["kind"] == "startup":
                state.update(HANDLERS[ep["kind"]](ep, res, prev, baseline, changes, state))
            else:
                state.update(HANDLERS[ep["kind"]](ep, res, prev, baseline, changes))
        except Exception as e:
            errors.append(f"{ep['id']} parse: {type(e).__name__}: {e}")

    # ---- availability: HEAD/1-byte probes only, never downloads ----
    targets = {}
    codev = state.get("online", {}).get("codeV", "")
    if codev:
        targets[f"fastpatch:{codev}"] = f"{GDL_HOST}/fastpatch/trunk/{codev}/fastpatch.zip"
    for pid, files in state.get("startup", {}).get("files", {}).items():
        for name in files:
            targets[f"startup:{name}"] = f"{GPH_HOST}/{name}"
    prev_avail = prev.get("availability", {})
    avail = {}
    for label, url in targets.items():
        status, ok = check_url(url)
        avail[label] = {"url": url, "status": status, "reachable": ok}
        was = prev_avail.get(label, {}).get("reachable")
        if not baseline and was is not None and was != ok:
            changes.append(f"availability flip {label}: "
                           f"{'reachable' if was else 'unreachable'} -> "
                           f"{'reachable' if ok else 'unreachable'} ({status})")
    state["availability"] = avail

    if errors:
        state["last_error"] = "; ".join(errors)
        state["consec_failures"] = int(prev.get("consec_failures", 0)) + 1
    else:
        state.pop("last_error", None)
        state["consec_failures"] = 0
    with open(os.path.join(ROOT, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    if baseline:
        line = (f"[baseline] {now} "
                f"online={state.get('online', {}).get('resV')} "
                f"outertest1={state.get('outertest1', {}).get('resV')}")
        log_change(line)
        notify(line)
    elif changes:
        for c in changes:
            log_change(f"{now} {c}")
        notify("[ananta] new build detected\n" + "\n".join(f"- {c}" for c in changes))
    elif state.get("consec_failures", 0) >= 3:
        notify(f"[ananta] endpoints failing x{state['consec_failures']}: {state.get('last_error')}")
    else:
        print("no change", flush=True)
    if errors and state.get("consec_failures", 0) < 3:
        print("errors: " + "; ".join(errors), flush=True)


if __name__ == "__main__":
    sys.exit(main())
