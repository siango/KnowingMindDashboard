"""Independent fixture tests: no real Admin session, JWT, store or task is used."""
import asyncio
from unittest import mock

from av15 import mcpv5_kmcw_authenticated as bridge


def test_email_pair_required():
    assert not bridge._identity_match({"email":"a@example.org"}, {})
    assert not bridge._identity_match({}, {"authenticated":True,"email":"a@example.org"})
    assert not bridge._identity_match({"email":"a@example.org"},
                                      {"authenticated":True,"email":"b@example.org"})
    assert bridge._identity_match({"email":"A@example.org"},
                                  {"authenticated":True,"email":"a@example.org"})


def test_missing_admin_credentials_denies_dispatch():
    from coding_worker_v2.agent.capabilities import KMAdminContextProducer
    from coding_worker_v2.agent.tools import ToolError
    with mock.patch.object(KMAdminContextProducer, "from_runtime_credentials", return_value=None):
        try:
            bridge.execute_bound_read("queue_health", {"email":"a@example.org"},
                                      ({}, mock.Mock()))
        except ToolError as exc:
            assert exc.reason == "KM_ADMIN_SESSION_CREDENTIAL_UNAVAILABLE"
        else:
            raise AssertionError("ungranted execution")


def test_mismatch_denies_before_delegation():
    from coding_worker_v2.agent.capabilities import KMAdminContextProducer
    from coding_worker_v2.agent.tools import ToolError
    producer = mock.Mock()
    producer._read.return_value = {"authenticated":True,"email":"other@example.org"}
    with mock.patch.object(KMAdminContextProducer, "from_runtime_credentials",
                           return_value=producer):
        try:
            bridge.execute_bound_read("queue_health", {"email":"a@example.org"},
                                      ({}, mock.Mock()))
        except ToolError as exc:
            assert exc.reason == "MCP_CLOUDFLARE_ADMIN_IDENTITY_MISMATCH"
        else:
            raise AssertionError("identity mismatch granted")
    producer._read.assert_called_once_with("/api/session")


def test_no_granted_tool_denies_before_call():
    from coding_worker_v2.agent.capabilities import KMAdminContextProducer
    from coding_worker_v2.agent.tools import ToolError
    producer = mock.Mock()
    producer._read.return_value = {"authenticated":True,"email":"a@example.org"}
    ctx = {"command_auth":{"permissions":[]}}
    with mock.patch.object(KMAdminContextProducer,"from_runtime_credentials",
                           return_value=producer), \
         mock.patch("coding_worker_v2.agent.capabilities.bind_runtime_context",
                    return_value=ctx), \
         mock.patch("coding_worker_v2.agent.mcp_server._call") as dispatcher:
        try:
            bridge.execute_bound_read("queue_health", {"email":"a@example.org"},
                                      ({}, mock.Mock()))
        except ToolError as exc:
            assert exc.reason == "MCP_TOOL_SCOPE_DENIED"
        else:
            raise AssertionError("permission not granted")
    dispatcher.assert_not_called()


def _request(headers=(), accepted=False):
    events, reached = [], []
    async def fake_app(scope, receive, send):
        reached.append(True)
        await send({"type":"http.response.start", "status":200, "headers":[]})
        await send({"type":"http.response.body","body":b""})
    async def receive():
        return {"type":"http.request","body":b"","more_body":False}
    async def send(event):
        events.append(event)
    with mock.patch.object(bridge.secure, "validate_access_jwt") as validate:
        if accepted:
            validate.return_value = {"sub":"fixture", "email":"a@example.org"}
        else:
            validate.side_effect = ValueError("bad-token")
        asyncio.run(bridge.IdentityGate(fake_app)(
            {"type":"http","path":"/mcp","headers":list(headers)}, receive,send))
    return events[0]["status"], reached


def test_anonymous_http_denied():
    status, reached = _request()
    assert status == 401 and not reached


def test_duplicate_auth_headers_denied():
    status, reached = _request([
        (b"authorization",b"Bearer a"),(b"authorization",b"Bearer a")], accepted=True)
    assert status == 401 and not reached


def test_validated_claims_carried_in_request_only():
    status, reached = _request([(b"cf-access-jwt-assertion",b"fixture")],
                                accepted=True)
    assert status == 200 and reached == [True]


def test_client_supplied_tool_arguments_not_supported():
    import inspect
    sig = inspect.signature(bridge.kmcw_authorized_read)
    assert list(sig.parameters) == ["tool_name", "ctx"]


def test_authorized_read_dispatches_issuer_bound_arguments_only():
    from coding_worker_v2.agent.capabilities import KMAdminContextProducer
    producer = mock.Mock()
    producer._read.return_value = {"authenticated": True, "email": "a@example.org"}
    ctx = {"command_auth": {"permissions": ["queue_health"]},
           "tool_bindings": {"queue_health": {"arguments": {"issuer_bound": True}}}}
    registry = mock.Mock()
    registry.command_contract.return_value = {
        "commands": [{"name": "queue_health", "permission": "read"}]}
    with mock.patch.object(KMAdminContextProducer, "from_runtime_credentials",
                           return_value=producer), \
         mock.patch("coding_worker_v2.agent.capabilities.bind_runtime_context",
                    return_value=ctx), \
         mock.patch("coding_worker_v2.agent.capabilities.authorized", return_value=True), \
         mock.patch("coding_worker_v2.agent.mcp_server._call",
                    return_value={"ok":True}) as dispatcher:
        output = bridge.execute_bound_read("queue_health",
                     {"email":"a@example.org"}, ({}, registry))
    assert output["ok"] is True
    assert output["verified_receipt"] is False
    args = dispatcher.call_args.args
    assert args[1] == {"name":"queue_health",
                       "arguments":{"issuer_bound":True}}
    assert args[2]["read_only"] is True
