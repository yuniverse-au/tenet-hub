"""Small HTTPS helpers (standard library only)."""
import json, urllib.parse, urllib.request

from . import VERSION

UA = f"TENET-Hub/{VERSION}"


def get(url, timeout=30, limit=50_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url} is unexpectedly large")
    return data


def get_json(url, timeout=30):
    return json.loads(get(url, timeout).decode("utf-8"))


def resolve(base, url):
    """Index entries may use paths relative to the index file."""
    return urllib.parse.urljoin(base, url)
