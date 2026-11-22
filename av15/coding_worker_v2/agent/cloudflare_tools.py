"""Opt-in Cloudflare provider tools on the original registry; no credential issuer."""
from __future__ import annotations
import json
import re
from urllib import request, error
from .tools import ToolError, ToolSpec

SERVICES = {
    "workers": ("workers/scripts", ("id", "created_on", "modified_on")),
    "d1": ("d1/database", ("uuid", "name", "version")),
    "r2": ("r2/buckets", ("name", "creation_date")),
    "queues": ("queues", ("queue_id", "queue_name", "created_on")),
    "tunnels": ("cfd_tunnel", ("id", "name", "status")),
    "containers": ("containers/applications", ("id", "name")),
    "ai_models": ("ai/models/search", ("id", "name", "description")),
}
PAGED = {"d1", "queues", "tunnels", "ai_models"}


class CloudflareClient:
    """Trusted runtime supplies one provider account and a native credential getter.

    This binding provides transport, not a principal, approval or MCP grant.
    No token, arbitrary endpoint, account or HTTP method is taken from tool args.
    """
    def __init__(self, account_id, token_provider, *, allowed_models=(), opener=None):
        if (not isinstance(account_id, str) or not re.fullmatch(r"[a-f0-9]{32}", account_id)
                or not callable(token_provider)
                or not isinstance(allowed_models, tuple)
                or any(not isinstance(m, str) or not re.fullmatch(r"@cf/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", m)
                       for m in allowed_models)):
            raise ToolError("CF_BINDING_INVALID")
        self.account_id = account_id
        self._token_provider = token_provider
        self.allowed_models = allowed_models
        if opener is None:
            class NoRedirect(request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            opener = request.build_opener(NoRedirect()).open
        if not callable(opener):
            raise ToolError("CF_BINDING_INVALID")
        self._open = opener

    def _call(self, path, body=None):
        try:
            token = self._token_provider()
        except Exception:
            raise ToolError("CF_CREDENTIAL_UNAVAILABLE") from None
        if (not isinstance(token, str) or not 20 <= len(token) <= 8192
                or not re.fullmatch(r"[A-Za-z0-9._-]+", token)):
            raise ToolError("CF_CREDENTIAL_INVALID")
        req = request.Request(
            "https://api.cloudflare.com/client/v4/accounts/" + self.account_id + "/" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=15) as response:
                raw = response.read(524289)
                if len(raw) > 524288:
                    raise ToolError("CF_RESPONSE_LIMIT")
                value = json.loads(raw)
        except error.HTTPError as exc:
            reason = {401: "CF_AUTH_REQUIRED", 403: "CF_SCOPE_DENIED",
                      404: "CF_RESOURCE_OR_API_UNAVAILABLE", 429: "CF_RATE_LIMITED"}.get(exc.code)
            raise ToolError(reason or "CF_HTTP_ERROR", str(exc.code)) from None
        except (error.URLError, TimeoutError, OSError):
            raise ToolError("CF_TRANSPORT_UNAVAILABLE") from None
        except (ValueError, UnicodeError):
            raise ToolError("CF_RESPONSE_INVALID") from None
        if not isinstance(value, dict) or value.get("success") is not True:
            raise ToolError("CF_PROVIDER_REJECTED")
        return value

    def inventory(self, service, *, page=1):
        if service not in SERVICES:
            raise ToolError("CF_SERVICE_NOT_ALLOWLISTED")
        if type(page) is not int or not 1 <= page <= 10:
            raise ToolError("CF_PAGE_INVALID")
        if service not in PAGED and page != 1:
            raise ToolError("CF_PAGINATION_UNSUPPORTED")
        endpoint, fields = SERVICES[service]
        if service in PAGED:
            endpoint += "?per_page=100&page=" + str(page)
        value = self._call(endpoint)
        rows = value.get("result")
        if isinstance(rows, dict) and service in {"r2", "containers"}:
            rows = rows.get("buckets" if service == "r2" else "applications")
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ToolError("CF_RESULT_SCHEMA_INVALID")
        items = []
        for row in rows[:100]:
            # Never forward raw bindings, env vars, credentials or provider blobs.
            safe = {key: row[key] for key in fields
                    if key in row and isinstance(row[key], (str, int, bool, type(None)))}
            if service == "ai_models" and isinstance(row.get("task"), dict):
                safe["task"] = row["task"].get("name") if isinstance(row["task"].get("name"), str) else None
            items.append(safe)
        info = value.get("result_info")
        info = {k: v for k, v in info.items()
                if k in {"page", "per_page", "count", "total_count", "total_pages"}
                and type(v) is int} if isinstance(info, dict) else {}
        total = info.get("total_count")
        complete = (page == 1 and type(total) is int and total <= len(items)
                    and len(rows) <= 100)
        return {"provider": "cloudflare", "account_ref": self.account_id,
                "service": service, "page": page, "items": items,
                "pagination": info, "complete": complete,
                "scope": "METADATA_ONLY", "independent_qc": "NOT_RUN"}

    def infer(self, model, prompt, *, max_tokens=16):
        if model not in self.allowed_models:
            raise ToolError("CF_MODEL_NOT_APPROVED")
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= 2048:
            raise ToolError("CF_PROMPT_LIMIT")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 64:
            raise ToolError("CF_TOKEN_LIMIT")
        value = self._call("ai/run/" + model, {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0})
        result = value.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("response"), str):
            raise ToolError("CF_AI_RESULT_INVALID")
        if len(result["response"].encode()) > 8192:
            raise ToolError("CF_AI_OUTPUT_LIMIT")
        return {"provider": "cloudflare", "account_ref": self.account_id,
                "model": model, "response": result["response"],
                "max_output_tokens": max_tokens, "billed_cost": None,
                "independent_qc": "NOT_RUN"}


def register(registry, client):
    if not isinstance(client, CloudflareClient):
        raise ToolError("CF_TRUSTED_CLIENT_REQUIRED")
    registry.register(ToolSpec("cf.inventory.read", "Cloudflare bounded service metadata",
        {"service": "str"}, {"page": "int"}, "read", "bounded provider API reads",
        "seven allowlisted services; page<=10; <=100 metadata rows; no raw bindings"),
        lambda args, ctx: client.inventory(args["service"], page=args.get("page", 1)))
    registry.register(ToolSpec("cf.ai.models", "Cloudflare Workers AI model metadata",
        {}, {"page": "int"}, "read", "bounded provider API read",
        "discovery is not inference readiness or model approval"),
        lambda args, ctx: client.inventory("ai_models", page=args.get("page", 1)))
    registry.register(ToolSpec("cf.ai.run", "Cloudflare scoped bounded inference",
        {"model": "str", "prompt": "str"}, {"max_tokens": "int"}, "write",
        "external inference and metered provider usage",
        "trusted model allowlist; prompt<=2048; output<=64 tokens; original write approval required over MCP"),
        lambda args, ctx: client.infer(args["model"], args["prompt"],
                                      max_tokens=args.get("max_tokens", 16)))
