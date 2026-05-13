from __future__ import annotations

import json
import sys
from urllib import error, request


REGISTER_URL = "http://localhost:8000/api/auth/register"
PAYLOAD = {"email": "demo@tradeflow.dev", "password": "demo1234"}


def main() -> int:
    body = json.dumps(PAYLOAD).encode("utf-8")
    req = request.Request(
        REGISTER_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with request.urlopen(req) as response:
            if response.status == 201:
                print("success")
                return 0
            print(f"unexpected response: {response.status}")
            return 1
    except error.HTTPError as exc:
        if exc.code == 409:
            print("already exists")
            return 0
        raw = exc.read().decode("utf-8", errors="replace")
        print(f"request failed: {exc.code} {raw}")
        return 1
    except error.URLError as exc:
        print(f"connection failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
