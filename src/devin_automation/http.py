# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""HTTPS-only HTTP and timestamp helpers."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

JsonDict = dict[str, Any]

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"
DEVIN_API = "https://api.devin.ai"


def _request(
    method: str, url: str, headers: dict[str, str], body: JsonDict | None = None
) -> Any:
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https URL: {url}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(  # noqa: S310
        url,
        data=data,
        headers={**headers, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} -> {exc.code}: {detail[:300]}") from exc


def _parse_ts(value: str | int | float | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
