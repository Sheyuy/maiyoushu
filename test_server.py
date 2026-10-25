"""本地代理的离线边界测试，不调用麦当劳远端。"""

import http.client
import json
import io
import socket
import time
import threading
import unittest
from email.message import Message
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import server

READ_AUTHORIZATION = server.read_authorization


def setUpModule():
    global NETWORK_GUARD
    NETWORK_GUARD = patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("离线测试禁止网络访问"))
    NETWORK_GUARD.start()
    global AUTH_GUARD
    AUTH_GUARD = patch.object(server, "read_authorization", side_effect=AssertionError("离线测试禁止读取真实授权"))
    AUTH_GUARD.start()


def tearDownModule():
    AUTH_GUARD.stop()
    NETWORK_GUARD.stop()


class StubMCP:
    def __init__(self):
        self.calls = []
        self.connected = True

    def initialize(self):
        if not self.connected:
            raise server.SafeError("官方 MCP 连接或响应异常")

    def schema(self, name):
        if name not in server.ALLOWED_TOOLS:
            raise server.SafeError("此工具不在允许列表", 403)
        return {"type": "object", "properties": {}, "additionalProperties": False}

    def call(self, name, arguments):
        if not isinstance(name, str) or name not in server.ALLOWED_TOOLS:
            raise server.SafeError("此工具不在允许列表", 403)
        self.calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "测试响应"}]}

    def public_bytes(self, value):
        return json.dumps(value, ensure_ascii=False).encode("utf-8")


class HTTPBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer((server.HOST, 0), server.Handler)
        cls.httpd.mcp = StubMCP()
        cls.worker = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.worker.join()

    def request(self, method, path, body=None, headers=None, token=True):
        conn = http.client.HTTPConnection(server.HOST, self.httpd.server_port, timeout=5)
        request_headers = {"Host": "127.0.0.1:8765"}
        if token:
            request_headers["X-Maiyoushu-Token"] = server.API_TOKEN
        request_headers.update(headers or {})
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        raw = response.read()
        status = response.status
        result_headers = dict(response.getheaders())
        conn.close()
        self.assertEqual(result_headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", result_headers)
        return status, raw, result_headers

    def raw_request(self, data):
        with socket.create_connection((server.HOST, self.httpd.server_port), timeout=3) as conn:
            conn.sendall(data)
            conn.shutdown(socket.SHUT_WR)
            raw = bytearray()
            while block := conn.recv(65536):
                raw.extend(block)
        return bytes(raw)

    def test_real_status_contract(self):
        status, raw, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["connected"])
        self.httpd.mcp.connected = False
        try:
            _, raw, _ = self.request("GET", "/api/status")
            self.assertFalse(json.loads(raw)["connected"])
        finally:
            self.httpd.mcp.connected = True

    def test_private_files_and_paths_are_not_served(self):
        for path in ("/server.py", "/test_server.py", "/README.md", "/.workbuddy/mcp.json",
                     "/../mcp.json", "/%2e%2e/mcp.json", "/assets/test.svg"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_host_and_origin_restrictions(self):
        for headers in ({"Host": "evil.example:8765"}, {"Origin": "null"},
                        {"Origin": "https://evil.example"},
                        {"Origin": "http://localhost:8765"},
                        {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("GET", "/api/status", headers=headers)[0], 403)
        self.assertEqual(self.request("GET", "/api/status", headers={"Origin": "http://127.0.0.1:8765"})[0], 200)

    def test_api_requires_runtime_token(self):
        token = server.API_TOKEN
        # 静态文件与页面本身不需要令牌；页面被注入本次运行令牌，磁盘文件不含令牌。
        for path in ("/", "/app.js", "/style.css"):
            self.assertEqual(self.request("GET", path, token=False)[0], 200)
        _, raw, _ = self.request("GET", "/", token=False)
        self.assertIn(('<meta name="maiyoushu-token" content="' + token + '">').encode(), raw)
        self.assertNotIn(token.encode(), (server.ROOT / "index.html").read_bytes())
        # 缺少、为空、错误或重复的令牌一律 403，且不触达上游。
        before = getattr(self.httpd.mcp, "calls", None)
        for headers in ({}, {"X-Maiyoushu-Token": ""}, {"X-Maiyoushu-Token": "not-the-token"},
                        {"X-Maiyoushu-Token": token + "x"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("GET", "/api/status", headers=headers, token=False)[0], 403)
                self.assertEqual(self.request("GET", "/api/schema?name=list-nutrition-foods", headers=headers, token=False)[0], 403)
                merged = dict(headers)
                merged["Content-Type"] = "application/json"
                body = json.dumps({"name": "list-nutrition-foods", "arguments": {}})
                self.assertEqual(self.request("POST", "/api/call", body, merged, token=False)[0], 403)
        duplicate = self.raw_request(b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n"
                                     b"X-Maiyoushu-Token: " + token.encode("ascii") + b"\r\n"
                                     b"X-Maiyoushu-Token: wrong\r\n\r\n")
        self.assertIn(b" 403 ", duplicate.split(b"\r\n")[0])
        after = getattr(self.httpd.mcp, "calls", None)
        if before is not None:
            self.assertEqual(len(after), len(before))
        # 正确令牌放行：不再被令牌门阻断（子类桩初始化受授权守卫影响时可能返回 500）。
        self.assertNotEqual(self.request("GET", "/api/status")[0], 403)

    def test_call_and_schema_contracts(self):
        payload = json.dumps({"name": "list-nutrition-foods", "arguments": {}})
        status, raw, _ = self.request("POST", "/api/call", payload, {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"ok": True, "result": {"content": [{"type": "text", "text": "测试响应"}]}})
        self.assertEqual(self.request("GET", "/api/schema?name=list-nutrition-foods")[0], 200)
        self.assertEqual(self.request("GET", "/api/schema?name=create-order")[0], 403)

    def test_forbidden_tools_cannot_reach_upstream(self):
        before = len(self.httpd.mcp.calls)
        for name in ("create-order", "auto-bind-coupons", "mall-create-order", "weixinpay", []):
            body = json.dumps({"name": name, "arguments": {}})
            self.assertEqual(self.request("POST", "/api/call", body, {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(len(self.httpd.mcp.calls), before)

    def test_content_type_size_and_json(self):
        self.assertEqual(self.request("POST", "/api/call", "{}")[0], 415)
        self.assertEqual(self.request("POST", "/api/call", "{}", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/call", "{", {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/call", "{}", {"Content-Type": "application/json", "Content-Length": str(server.MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.request("OPTIONS", "/api/call")[0], 405)


class ValidationTests(unittest.TestCase):
    def test_remote_schema_validation(self):
        schema = {"type": "object", "properties": {"beType": {"type": "integer", "enum": [1, 5]}},
                  "required": ["beType"], "additionalProperties": False}
        server.validate_schema({"beType": 1}, schema)
        for value in ({}, {"beType": True}, {"beType": 2}, {"beType": "1"}, {"beType": 1, "extra": 2}):
            with self.subTest(value=value), self.assertRaises(server.SafeError):
                server.validate_schema(value, schema)

    def test_call_whitelist_before_initialization(self):
        client = server.MCPClient()
        with patch.object(client, "initialize") as initialize:
            with self.assertRaises(server.SafeError):
                client.call("create-order", {})
            initialize.assert_not_called()

    def test_upstream_exception_is_sanitized(self):
        client = server.MCPClient()
        client.authorization = "Bearer synthetic-test-secret"
        with patch.object(client.opener, "open", side_effect=ValueError(client.authorization)):
            with self.assertRaises(server.SafeError) as error:
                client._exchange("tools/list", {})
        self.assertNotIn("synthetic", str(error.exception))
        with self.assertRaises(server.SafeError):
            client.public_bytes({"echo": client.authorization})
        with self.assertRaises(server.SafeError):
            client.public_bytes({"echo": "synthetic-test-secret"})


class FakeResponse(io.BytesIO):
    def __init__(self, payload, content_type="application/json", session=None):
        super().__init__(payload)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if session is not None:
            self.headers["Mcp-Session-Id"] = session

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def ready_client(schema=None):
    client = server.MCPClient()
    client.authorization = "Bearer synthetic-audit-secret"
    client.schemas = {name: schema if schema is not None else {"type": "object"}
                      for name in server.ALLOWED_TOOLS}
    return client


class AuditHTTPTests(HTTPBoundaryTests):
    def setUp(self):
        self.previous = self.httpd.mcp
        self.client = ready_client()
        self.httpd.mcp = self.client
        self.exchange = patch.object(self.client, "_exchange", return_value={"content": []})
        self.upstream = self.exchange.start()

    def tearDown(self):
        self.exchange.stop()
        self.httpd.mcp = self.previous

    # 基础契约由父类独立执行，这里只复用 HTTP 辅助方法。
    test_real_status_contract = None
    test_private_files_and_paths_are_not_served = None
    test_host_and_origin_restrictions = None
    test_call_and_schema_contracts = None
    test_forbidden_tools_cannot_reach_upstream = None
    test_content_type_size_and_json = None

    def post(self, body):
        return self.request("POST", "/api/call", body, {"Content-Type": "application/json"})

    def test_csp_on_success_and_error(self):
        for path in ("/", "/app.js", "/style.css", "/server.py"):
            with self.subTest(path=path):
                _, _, headers = self.request("GET", path)
                csp = headers.get("Content-Security-Policy", "")
                self.assertIn("default-src 'none'", csp)
                self.assertIn("script-src 'self'", csp)
                self.assertIn("frame-ancestors 'none'", csp)
                self.assertIn("connect-src 'self'", csp)
                self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)

    def test_parameter_shape_and_nonfinite_do_not_reach_upstream(self):
        bad = ['{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"x":1e400}',
               '{"x":[{"n":-1e400}]}', '[]', 'null', '"x"', 'true', '1']
        nested = '0'
        for _ in range(50):
            nested = '[' + nested + ']'
        bad.append('{"x":' + nested + '}')
        for args in bad:
            with self.subTest(arguments=args[:70]):
                body = '{"name":"list-nutrition-foods","arguments":' + args + '}'
                self.assertEqual(self.post(body)[0], 400)
        self.upstream.assert_not_called()

    def test_duplicate_keys_and_invalid_unicode(self):
        for body in ('{"name":"create-order","name":"list-nutrition-foods","arguments":{}}',
                     '{"name":"list-nutrition-foods","arguments":{"x":1,"x":2}}',
                     '{"name":"list-nutrition-foods","arguments":{"x":"\\ud800"}}'):
            with self.subTest(body=body):
                self.assertEqual(self.post(body)[0], 400)
        self.upstream.assert_not_called()

    def test_schema_query_rejects_blank_extra_fields(self):
        for query in ("name=list-nutrition-foods&extra=", "name=list-nutrition-foods&name=",
                      "name=list-nutrition-foods&name=query-meals"):
            with self.subTest(query=query):
                self.assertEqual(self.request("GET", "/api/schema?" + query)[0], 400)

    def test_raw_http_duplicate_headers_and_framing(self):
        token = b"X-Maiyoushu-Token: " + server.API_TOKEN.encode("ascii") + b"\r\n"
        variants = [(b"Host: 127.0.0.1:8765\r\n" + token + b"Host: evil.example", 403),
                    (b"Host: 127.0.0.1:8765\r\nOrigin: http://127.0.0.1:8765\r\nOrigin: null", 403),
                    (b"Host: 127.0.0.1:8765\r\nSec-Fetch-Site: none\r\nSec-Fetch-Site: cross-site", 403),
                    (b"Host: 127.0.0.1:8765\r\nTransfer-Encoding:", 400),
                    (b"Host: 127.0.0.1:8765\r\nTransfer-Encoding: chunked", 400)]
        for headers, status in variants:
            with self.subTest(headers=headers):
                raw = self.raw_request(b"GET /api/schema?name=list-nutrition-foods HTTP/1.1\r\n" + headers + b"\r\n\r\n")
                self.assertIn(b" " + str(status).encode() + b" ", raw.split(b"\r\n")[0])
        for framing in (b"Content-Length: 2\r\nContent-Length: 2", b"Content-Length: +2",
                        b"Content-Length: \xb2", b"Content-Length: " + b"9" * 5000):
            raw = self.raw_request(b"POST /api/call HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n" + token +
                                   b"Content-Type: application/json\r\n" + framing + b"\r\n\r\n{}")
            self.assertIn(b" 411 ", raw.split(b"\r\n")[0])
        raw = self.raw_request(b"POST /api/call HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n" + token +
                               b"Content-Type: application/json\r\nContent-Length: 10\r\n\r\n{}")
        self.assertIn(b" 400 ", raw.split(b"\r\n")[0])
        self.upstream.assert_not_called()

    def test_static_exact_allowlist_and_symlink(self):
        for path in ("/.env", "/outputs/audit-backend.json", "/mcp-config.example.json", "/app.js/..", "/%61pp.js", "/app.js?x=1", "/%2fserver.py", "/.workbuddy/runtime/backend-response-structure.json"):
            self.assertEqual(self.request("GET", path)[0], 404)
        with patch.object(server.Path, "is_symlink", return_value=True):
            self.assertEqual(self.request("GET", "/app.js")[0], 404)

    def test_all_nonwhitelisted_names_blocked(self):
        for name in ("create-order", "mall-create-order", "party-order-create", "auto-bind-coupons",
                     "delivery-create-address", "draw-lottery", "weixinpay", "tools/call", "query-meals ",
                     "QUERY-MEALS", "query-meals\u0000", [], {}, None, True, 1):
            self.assertEqual(self.post(json.dumps({"name": name, "arguments": {}}))[0], 403)
        self.upstream.assert_not_called()

    def test_credential_echo_blocked_at_http_boundary(self):
        secret = self.client.authorization[7:]
        self.upstream.return_value = {"content": [{"type": "text", "text": secret}]}
        status, raw, _ = self.post('{"name":"list-nutrition-foods","arguments":{}}')
        self.assertEqual(status, 502)
        self.assertNotIn(secret.encode(), raw)
        self.client.schemas["list-nutrition-foods"]["description"] = secret
        status, raw, _ = self.request("GET", "/api/schema?name=list-nutrition-foods")
        self.assertEqual(status, 502)
        self.assertNotIn(secret.encode(), raw)

    def test_mcp_tool_error_is_sanitized(self):
        self.upstream.return_value = {"isError": True, "content": [{"type": "text", "text": "private upstream diagnostics"}]}
        status, raw, _ = self.post('{"name":"list-nutrition-foods","arguments":{}}')
        self.assertEqual(status, 502)
        self.assertNotIn(b"private upstream", raw)


class AuditClientTests(unittest.TestCase):
    def test_jsonrpc_boolean_id_is_not_integer_id(self):
        with self.assertRaises(server.SafeError):
            server.MCPClient._result({"jsonrpc": "2.0", "id": True, "result": {}}, 1)

    def test_global_value_limits_without_schema_constraints(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(server.SafeError):
                server.validate_schema({"extra": [{"value": value}]}, {"type": "object"})
        value = 0
        for _ in range(50):
            value = [value]
        with self.assertRaises(server.SafeError):
            server.validate_schema({"extra": value}, {"type": "object"})

    def test_schema_semantics_numeric_equality(self):
        server.validate_schema(1.0, {"type": "integer", "enum": [1]})
        server.validate_schema({"x": 1.0}, {"const": {"x": 1}})
        for value in ([1, 1.0], [{"x": True}, {"x": 1}]):
            with self.subTest(value=value):
                if isinstance(value[0], dict):
                    server.validate_schema(value, {"uniqueItems": True})
                else:
                    with self.assertRaises(server.SafeError):
                        server.validate_schema(value, {"uniqueItems": True})
        with self.assertRaises(server.SafeError):
            server.validate_schema({"x": True}, {"const": {"x": 1}})
        with self.assertRaises(server.SafeError):
            server.validate_schema(1.0000000001, {"multipleOf": 1})

    def test_invalid_or_unsupported_schema_fails_closed(self):
        for schema in ({"format": "email"}, {"type": "bogus"}, {"required": "x"},
                       {"properties": []}, {"multipleOf": 0}, {"pattern": "["},
                       {"pattern": "a" * 201}, {"anyOf": []}, {"items": []},
                       {"minimum": "1"}, {"$ref": "#/missing"},
                       {"properties": {"absent": {"unevaluatedProperties": False}}}):
            with self.subTest(schema=schema):
                with self.assertRaises(server.SafeError) as error:
                    server.validate_schema({}, schema)
                self.assertEqual(error.exception.status, 502)

    def test_credential_escaping_and_schema_echo(self):
        client = ready_client()
        for value in ({"echo": client.authorization}, {"echo": client.authorization[7:]},
                      {"echo": json.dumps(client.authorization[7:])[1:-1]},
                      {"echo": "synthetic-audit-\\u0073ecret"}):
            with self.subTest(value=value), self.assertRaises(server.SafeError):
                client.public_bytes(value)

    def test_json_and_sse_response_validation(self):
        for content_type, payload, valid in (
                ("application/json", b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}', True),
                ("text/event-stream", b': ping\n\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\nevent: message\ndata: {"jsonrpc":"2.0","id":1,\ndata: "result":{"ok":true}}\n\n', True),
                ("application/json", b'{"jsonrpc":"2.0","id":2,"result":{}}', False),
                ("application/json", b'{"jsonrpc":"2.0","id":1,"result":{},"error":{}}', False),
                ("application/json", b'{"jsonrpc":"2.0","id":1,"result":{"n":1e400}}', False),
                ("text/html", b'<html>synthetic-audit-secret</html>', False),
                ("text/event-stream", b'data: {"jsonrpc":"2.0","id":1,"result":{}}\n', False)):
            with self.subTest(content_type=content_type, payload=payload):
                client = ready_client()
                with patch.object(client.opener, "open", return_value=FakeResponse(payload, content_type)):
                    if valid:
                        self.assertEqual(client._exchange("tools/list", {}), {"ok": True})
                    else:
                        with self.assertRaises(server.SafeError):
                            client._exchange("tools/list", {})

    def test_upstream_http_errors_and_timeout_are_sanitized(self):
        import urllib.error
        for exception, status in ((TimeoutError("synthetic-audit-secret"), 504),
                                  (urllib.error.HTTPError(server.REMOTE, 401, "synthetic-audit-secret", {}, None), 503),
                                  (urllib.error.HTTPError(server.REMOTE, 404, "synthetic-audit-secret", {}, None), 503),
                                  (urllib.error.HTTPError(server.REMOTE, 500, "synthetic-audit-secret", {}, None), 502)):
            client = ready_client()
            client.session = "synthetic-session"
            with patch.object(client.opener, "open", side_effect=exception):
                with self.assertRaises(server.SafeError) as error:
                    client._exchange("tools/list", {})
                self.assertEqual(error.exception.status, status)
                self.assertNotIn("synthetic", str(error.exception))
            if status == 503:
                self.assertFalse(client.schemas)
                self.assertIsNone(client.session)

    def test_authorization_config_shape_and_header_safety(self):
        from pathlib import Path
        # 模块级授权读取守卫之下，仅在此处调用原函数；配置内容全部由 mock 提供。
        original = READ_AUTHORIZATION
        configs = [[], {"mcpServers": {"other": {"url": server.REMOTE, "headers": {"Authorization": "Bearer bad\x00value"}},
                                        "official": {"url": server.REMOTE, "headers": {"Authorization": "Bearer synthetic-config-secret"}}}},
                   {"mcpServers": {"official": {"url": server.REMOTE, "headers": {"Authorization": "Bearer synthetic-config-secret"}}}}]
        with patch.dict(server.os.environ, {"MCD_MCP_TOKEN": ""}), patch.object(server, "CONFIG_PATHS", [Path("mock-config")]), patch.object(Path, "is_file", return_value=True):
            for config in configs:
                with self.subTest(config_type=type(config).__name__), patch.object(Path, "read_text", return_value=json.dumps(config)):
                    if isinstance(config, list):
                        with self.assertRaises(server.SafeError):
                            original()
                    else:
                        self.assertEqual(original(), "Bearer synthetic-config-secret")
        with patch.object(server.os, "environ", {"MCD_MCP_TOKEN": "bad\x00value"}), patch.object(server, "CONFIG_PATHS", []):
            with self.assertRaises(server.SafeError):
                original()

    def test_initialization_pagination_and_atomic_schema_cache(self):
        tools = [{"name": name, "inputSchema": {"type": "object"}} for name in sorted(server.ALLOWED_TOOLS)]
        client = server.MCPClient()
        replies = [{"protocolVersion": "2025-03-26"}, None,
                   {"tools": tools[:3], "nextCursor": "page2"}, {"tools": tools[3:] + [{"name": "create-order"}]}]
        with patch.object(server, "read_authorization", return_value="Bearer synthetic-audit-secret"), patch.object(client, "_exchange", side_effect=replies) as exchange:
            client.initialize()
            self.assertEqual(set(client.schemas), server.ALLOWED_TOOLS)
            self.assertEqual(exchange.call_args_list[-1].args, ("tools/list", {"cursor": "page2"}))
        for listing in ({"tools": tools + [tools[0]]}, {"tools": tools[:-1]},
                        {"tools": tools, "nextCursor": []}, {"tools": [None]},
                        {"tools": [{"name": []}]},
                        {"tools": [{"name": tools[0]["name"], "inputSchema": {"format": "email"}}]}):
            with self.subTest(listing=listing):
                client = ready_client()
                with patch.object(server, "read_authorization", return_value="Bearer synthetic-audit-secret"), patch.object(client, "_exchange", side_effect=[{"protocolVersion": "2025-03-26"}, None, listing]):
                    with self.assertRaises(server.SafeError):
                        client.initialize()
                    self.assertEqual(client.schemas, {})

    def test_request_headers_session_and_no_redirect(self):
        client = ready_client()
        response = FakeResponse(b'{"jsonrpc":"2.0","id":1,"result":{}}', session="session-123")
        with patch.object(client.opener, "open", return_value=response) as opened:
            client._exchange("initialize", {})
            req = opened.call_args.args[0]
            self.assertEqual(req.full_url, server.REMOTE)
            self.assertEqual(req.get_header("Authorization"), client.authorization)
            self.assertIsNone(req.get_header("Mcp-protocol-version"))
        with patch.object(client.opener, "open", return_value=FakeResponse(b'{"jsonrpc":"2.0","id":2,"result":{}}')) as opened:
            client._exchange("tools/list", {})
            req = opened.call_args.args[0]
            self.assertEqual(req.get_header("Mcp-session-id"), "session-123")
            self.assertEqual(req.get_header("Mcp-protocol-version"), "2025-03-26")
        for code in (301, 302, 303, 307, 308):
            with self.assertRaises(server.SafeError):
                server.NoRedirect().redirect_request(None, None, code, "", {}, "https://evil.example")
        for session in ("bad\r\nheader", "", "x" * 1025):
            client = ready_client()
            with patch.object(client.opener, "open", return_value=FakeResponse(b"{}", session=session)):
                with self.assertRaises(server.SafeError):
                    client._exchange("initialize", {})

    def test_response_size_and_total_deadline(self):
        for content_type in ("application/json", "text/event-stream"):
            client = ready_client()
            with patch.object(server, "MAX_REMOTE", 64), patch.object(client.opener, "open", return_value=FakeResponse(b"x" * 65, content_type)):
                with self.assertRaises(server.SafeError):
                    client._exchange("tools/list", {})
        class DripResponse(FakeResponse):
            def read1(self, size):
                clock[0] += 0.02
                return b":"
        for content_type in ("application/json", "text/event-stream"):
            clock = [100.0]
            client = ready_client()
            with patch.object(server.time, "monotonic", side_effect=lambda: clock[0]), patch.object(server, "REQUEST_TIMEOUT", 0.05), patch.object(client.opener, "open", return_value=DripResponse(b"", content_type)):
                with self.assertRaises(server.SafeError) as error:
                    client._exchange("tools/list", {})
                self.assertEqual(error.exception.status, 504)
                self.assertLess(clock[0], 100.1)

    def test_concurrent_calls_preserve_session_sequence(self):
        client = ready_client()
        active = 0
        peak = 0
        ids = []
        def respond(request, timeout):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            packet = json.loads(request.data)
            ids.append(packet["id"])
            threading.Event().wait(0.005)
            active -= 1
            return FakeResponse(json.dumps({"jsonrpc": "2.0", "id": packet["id"], "result": {"content": []}}).encode())
        with patch.object(client.opener, "open", side_effect=respond):
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: client.call("list-nutrition-foods", {}), range(16)))
        self.assertEqual(len(results), 16)
        self.assertEqual(ids, list(range(1, 17)))
        self.assertEqual(peak, 1)
        self.assertIsNone(client.deadline)

    def test_connection_worker_limit(self):
        # 用隔离端口构造真实 LocalServer，绝不绑定或关闭主服务端口。
        with patch.object(server, "PORT", 0):
            httpd = server.LocalServer()
        sockets = []
        try:
            httpd.workers = threading.BoundedSemaphore(1)
            httpd.timeout = 1
            first = socket.create_connection(httpd.server_address, timeout=2)
            sockets.append(first)
            httpd.handle_request()
            second = socket.create_connection(httpd.server_address, timeout=2)
            sockets.append(second)
            httpd.handle_request()
            self.assertEqual(second.recv(1), b"")
        finally:
            for conn in sockets:
                conn.close()
            httpd.server_close()

    def test_busy_client_has_bounded_wait(self):
        client = ready_client()
        locked = threading.Event()
        release = threading.Event()
        def hold():
            with client.lock:
                locked.set()
                release.wait(3)
        worker = threading.Thread(target=hold)
        worker.start()
        self.assertTrue(locked.wait(1))
        try:
            with patch.object(server, "REQUEST_TIMEOUT", 0.05, create=True):
                start = time.monotonic()
                with self.assertRaises(server.SafeError) as error:
                    client.call("list-nutrition-foods", {})
                self.assertEqual(error.exception.status, 504)
                self.assertLess(time.monotonic() - start, 0.5)
        finally:
            release.set()
            worker.join()


class ApiTokenUnitTests(unittest.TestCase):
    def test_token_generation_and_override(self):
        with patch.dict(server.os.environ, {"MAIYOUSHU_API_TOKEN": "custom-token-value-0123456789"}):
            self.assertEqual(server.load_api_token(), "custom-token-value-0123456789")
        for weak in ("short", "x" * 300, "bad token with spaces", ""):
            with self.subTest(value=weak[:20]), patch.dict(server.os.environ, {"MAIYOUSHU_API_TOKEN": weak}):
                token = server.load_api_token()
                self.assertNotEqual(token, weak)
                self.assertGreaterEqual(len(token), 32)
                self.assertTrue(server.TOKEN_PATTERN.fullmatch(token))
        first, second = server.load_api_token(), server.load_api_token()
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
