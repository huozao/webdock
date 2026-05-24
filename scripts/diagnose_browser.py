from __future__ import annotations

import json
import os
import urllib.request


def main() -> None:
    base_url = os.getenv("BASE_URL", "http://localhost:8000")
    token = os.getenv("API_TOKEN", "")
    request = urllib.request.Request(f"{base_url}/browser-status")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
