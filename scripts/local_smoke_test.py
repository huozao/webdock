from __future__ import annotations

import json
import os
import urllib.request


def get_json(url: str, token: str = "") -> dict:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    base_url = os.getenv("BASE_URL", "http://localhost:8000")
    token = os.getenv("API_TOKEN", "")
    for path in ("/healthz", "/browser-status"):
        payload = get_json(f"{base_url}{path}", token)
        print(path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
