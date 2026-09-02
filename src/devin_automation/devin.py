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

"""Client for the Devin v3 API."""

from __future__ import annotations

import os
import urllib.parse
from datetime import datetime
from typing import Any

from .http import DEVIN_API, _request
from .models import JsonDict


class DevinClient:
    def __init__(self, api_key: str, org_id: str) -> None:
        self.enabled = bool(api_key and org_id)
        self.org_id = org_id
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def _url(self, path: str) -> str:
        return f"{DEVIN_API}/v3/organizations/{self.org_id}{path}"

    def sessions(self, repo: str, since: datetime) -> list[JsonDict]:
        if not self.enabled:
            return []
        params: dict[str, Any] = {
            "first": "200",
            "repo_names": repo,
            "created_after": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        results: list[JsonDict] = []
        after: str | None = None
        while True:
            if after:
                params["after"] = after
            query = urllib.parse.urlencode(params, doseq=True)
            data = _request("GET", self._url(f"/sessions?{query}"), self.headers)
            results.extend(data.get("items", []))
            if not data.get("has_next_page"):
                return results
            after = data.get("end_cursor")

    def automations(self) -> list[JsonDict]:
        if not self.enabled:
            return []
        data = _request("GET", self._url("/automations?first=100"), self.headers)
        return list(data.get("items", []))

    def create_session(
        self, prompt: str, title: str, tags: list[str], max_acu: int
    ) -> JsonDict:
        if not self.enabled:
            raise RuntimeError("DEVIN_API_KEY / DEVIN_ORG_ID not configured")
        body: JsonDict = {
            "prompt": prompt,
            "title": title,
            "tags": tags,
            "max_acu_limit": max_acu,
        }
        if user_id := os.environ.get("DEVIN_CREATE_AS_USER_ID"):
            body["create_as_user_id"] = user_id
        created: JsonDict = _request("POST", self._url("/sessions"), self.headers, body)
        return created


# --------------------------------------------------------------------------- #
