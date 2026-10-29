# -*- coding: utf-8 -*-
"""KB Securities Open API connection module (standard library only).

Usage from your own script:
    import kb_api as kb
    resp = kb.call("szqm0771", {})          # POST /api/v1/<api>, returns the parsed JSON
    body = kb.body_of(resp)                 # the dataBody part
    rows = kb.find_list(body)               # first list of dicts in the body
Keys are read from .env.txt (fill in the template next to this file). The access token is cached in .token_cache.json.
Wait kb.CALL_INTERVAL seconds between calls (the rate limit is not published).
"""
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SAMPLE_DIR = os.path.join(DATA_DIR, "raw_samples")
TOKEN_CACHE = os.path.join(BASE_DIR, ".token_cache.json")


# ---------------------------------------------------------------- config
def load_env(path):
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8-sig") as f:  # utf-8-sig: Notepad may add a BOM
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# The key file is ".env.txt" (a later name overrides an earlier one, so ".env.txt" wins over the old names).
ENV_NAMES = ("kb_keys.txt", "env.txt", ".env", ".env.txt")
ENV = {}
for _n in ENV_NAMES:
    ENV.update(load_env(os.path.join(BASE_DIR, _n)))


def cfg(name, default=""):
    v = os.environ.get(name) or ENV.get(name)
    return v if v not in (None, "") else default


BASE_URL = cfg("KB_BASE_URL", "https://developer.kbsec.com:32484").rstrip("/")
APP_KEY = cfg("KB_APP_KEY").strip()
APP_SECRET = cfg("KB_APP_SECRET").strip()
EXCG_CLSF = cfg("KB_EXCG_CLSF", "0")
CALL_INTERVAL = float(cfg("CALL_INTERVAL", "0.4"))


def local_ip():
    forced = cfg("KB_IP_ADDR")
    if forced:
        return forced
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is actually sent
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def local_mac():
    forced = cfg("KB_MAC_ADDR")
    if forced:
        return forced
    h = "%012X" % uuid.getnode()
    return "-".join(h[i:i + 2] for i in range(0, 12, 2))


# ---------------------------------------------------------------- http
class ApiError(Exception):
    pass


def http_post(url, payload, headers, timeout=20, raw=None):
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def get_token(force=False):
    if not APP_KEY or not APP_SECRET:
        raise ApiError("KB_APP_KEY / KB_APP_SECRET are empty. Fill in .env.txt first.")
    if not force and os.path.exists(TOKEN_CACHE):
        try:
            with open(TOKEN_CACHE, "r", encoding="utf-8") as f:
                c = json.load(f)
            if c.get("expires_at", 0) > time.time() + 300 and c.get("app_key") == APP_KEY:
                return c["access_token"]
        except Exception:
            pass
    cred = {"grantType": "client_credentials", "appKey": APP_KEY, "appSecret": APP_SECRET}
    attempts = [
        ("flat JSON", json.dumps(cred).encode("utf-8"), "application/json"),
        ("dataHeader/dataBody JSON", json.dumps({"dataHeader": {"ipAddr": local_ip(), "macAddr": local_mac()},
                                                 "dataBody": cred}).encode("utf-8"), "application/json"),
        ("form-urlencoded", urllib.parse.urlencode(cred).encode("utf-8"), "application/x-www-form-urlencoded"),
    ]
    token, tb, errors = None, {}, []
    for name, raw, ctype in attempts:
        status, text = http_post(BASE_URL + "/oauth2/token", None, {"Content-Type": ctype}, raw=raw)
        try:
            j = json.loads(text)
        except Exception:
            errors.append("%s: HTTP %s, not JSON: %s" % (name, status, text[:120]))
            continue
        # The server wraps the result as {"dataHeader": {...}, "dataBody": {"access_token": ...}}
        tb = j.get("dataBody") if isinstance(j.get("dataBody"), dict) else j
        token = tb.get("access_token")
        if token:
            print("Token OK (request format: %s)" % name)
            break
        h = j.get("dataHeader") or {}
        errors.append("%s: HTTP %s code=%s message=%s" % (name, status, h.get("processCode"),
                                                          h.get("processMessage") or text[:120]))
    if not token:
        raise ApiError("Token request failed with every format:\n  " + "\n  ".join(errors))
    with open(TOKEN_CACHE, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "app_key": APP_KEY,
                   "expires_at": time.time() + int(tb.get("expires_in") or 86400)}, f)
    return token


_token = None
_sample_count = {}


def call(api, body, retries=3):
    """POST /api/v1/<api> with dataHeader/dataBody. Returns the parsed JSON."""
    global _token
    if _token is None:
        _token = get_token()
    payload = {"dataHeader": {"ipAddr": local_ip(), "macAddr": local_mac()}, "dataBody": body}
    last = None
    for attempt in range(retries):
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + _token}
        try:
            status, text = http_post(BASE_URL + "/api/v1/" + api, payload, headers)
            if status == 401 and attempt == 0:
                _token = get_token(force=True)
                continue
            j = json.loads(text)
            if status >= 400:
                raise ApiError("HTTP %s: %s" % (status, text[:200]))
            save_sample(api, j)
            return j
        except (ApiError, json.JSONDecodeError, urllib.error.URLError, socket.timeout, OSError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise ApiError("%s failed after %d tries: %s" % (api, retries, last))


def save_sample(api, j):
    n = _sample_count.get(api, 0)
    if n >= 2:
        return
    _sample_count[api] = n + 1
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    with open(os.path.join(SAMPLE_DIR, "%s_%d.json" % (api, n + 1)), "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- parsing
def body_of(resp):
    """The document does not show the response envelope, so accept both shapes."""
    if isinstance(resp, dict) and isinstance(resp.get("dataBody"), dict):
        return resp["dataBody"]
    return resp if isinstance(resp, dict) else {}


def find_list(body):
    if isinstance(body.get("out"), list):
        return body["out"]
    for v in body.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


