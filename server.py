"""麦有数本地只读 MCP 代理，仅使用 Python 标准库。"""

import copy
import hmac
import json
import secrets
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765
ROOT = Path(__file__).resolve().parent
REMOTE = "https://mcp.mcd.cn"
CONFIG_PATHS = (
    Path.home() / ".workbuddy" / "mcp.json",
    ROOT / ".workbuddy" / "mcp.json",
)
ALLOWED_TOOLS = frozenset({
    "query-nearby-stores", "query-meals", "query-store-coupons",
    "query-meal-detail", "list-nutrition-foods", "calculate-price",
})
STATIC_FILES = {"/": "index.html", "/index.html": "index.html",
                "/app.js": "app.js", "/style.css": "style.css"}
MAX_BODY = 256 * 1024
MAX_REMOTE = 16 * 1024 * 1024
REQUEST_TIMEOUT = 45
MAX_WORKERS = 16
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' https://mcd.cn https://*.mcd.cn; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'")
API_TOKEN_HEADER = "X-Maiyoushu-Token"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/-]+=*")


def load_api_token():
    """优先使用环境变量指定的令牌；否则每次运行随机生成，只经所服务页面下发。"""
    value = os.environ.get("MAIYOUSHU_API_TOKEN", "").strip()
    if 16 <= len(value) <= 256 and TOKEN_PATTERN.fullmatch(value):
        return value
    return secrets.token_urlsafe(32)


API_TOKEN = load_api_token()


class SafeError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.message = message
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SafeError("官方服务重定向已阻止")


def valid_token(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value))


def read_authorization():
    """只读取环境变量或约定配置，不扫描其他路径。"""
    token = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if valid_token(token):
        return "Bearer " + token
    for path in CONFIG_PATHS:
        if not path.is_file():
            continue
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
            servers = config.get("mcpServers", {})
            if not isinstance(servers, dict):
                continue
            for server in servers.values():
                if not isinstance(server, dict):
                    continue
                if server.get("url", "").rstrip("/") != REMOTE:
                    continue
                headers = server.get("headers", {})
                if not isinstance(headers, dict):
                    continue
                for key, value in headers.items():
                    if str(key).lower() == "authorization" and isinstance(value, str):
                        if value.startswith("Bearer ") and valid_token(value[7:]):
                            return value
        except (OSError, ValueError, AttributeError):
            continue
    raise SafeError("未找到可用的官方 MCP 认证配置", 503)


def reject_constant(value):
    raise ValueError("不支持非有限数值")


def validate_value(value, status=400):
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 40 or nodes > 20000:
            raise SafeError("JSON 嵌套或复杂度超过限制", status)
        if isinstance(item, float) and not math.isfinite(item):
            raise SafeError("不支持非有限数值", status)
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise SafeError("JSON 文本编码无效", status) from None
        elif isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise SafeError("JSON 对象键必须是文本", status)
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise SafeError("不支持此 JSON 值", status)


def decode_json(value):
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("JSON 对象包含重复键")
            result[key] = item
        return result
    result = json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_object)
    validate_value(result)
    return result


def json_equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


def check_schema(schema, root=None, active=None):
    root = schema if root is None else root
    active = set() if active is None else active
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict) or id(schema) in active or len(active) > 40:
        raise SafeError("官方 schema 无效或引用过深")
    supported = {"$schema", "$id", "$defs", "definitions", "$ref", "title", "description",
                 "default", "examples", "deprecated", "readOnly", "writeOnly", "type", "enum",
                 "const", "properties", "required", "additionalProperties", "items", "minItems",
                 "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum",
                 "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "allOf",
                 "anyOf", "oneOf", "not"}
    if set(schema) - supported:
        raise SafeError("官方 schema 包含暂不支持的校验规则")
    active = active | {id(schema)}
    types = schema.get("type", [])
    types = types if isinstance(types, list) else [types]
    if ("type" in schema and not types) or any(not isinstance(t, str) or t not in {
            "object", "array", "string", "boolean", "integer", "number", "null"} for t in types):
        raise SafeError("官方 schema 类型无效")
    for key in ("required", "enum"):
        if key in schema and (not isinstance(schema[key], list) or
                              (key == "enum" and not schema[key]) or
                              (key == "required" and (any(not isinstance(v, str) for v in schema[key]) or
                                                     len(set(schema[key])) != len(schema[key])))):
            raise SafeError("官方 schema 列表无效")
    for key in ("minItems", "maxItems", "minLength", "maxLength"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise SafeError("官方 schema 长度无效")
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if key in schema and (type(schema[key]) not in (int, float) or
                              (key == "multipleOf" and schema[key] <= 0)):
            raise SafeError("官方 schema 数值规则无效")
    if "uniqueItems" in schema and type(schema["uniqueItems"]) is not bool:
        raise SafeError("官方 schema 唯一性规则无效")
    if "pattern" in schema:
        try:
            if not isinstance(schema["pattern"], str) or len(schema["pattern"]) > 200:
                raise ValueError
            re.compile(schema["pattern"])
        except (TypeError, ValueError, re.error):
            raise SafeError("官方 schema 正则无效") from None
    for key in ("properties", "$defs", "definitions"):
        if key in schema:
            if not isinstance(schema[key], dict):
                raise SafeError("官方 schema 属性无效")
            for child in schema[key].values():
                check_schema(child, root, active)
    for key in ("items", "additionalProperties", "not"):
        if key in schema:
            check_schema(schema[key], root, active)
    for key in ("allOf", "anyOf", "oneOf"):
        if key in schema:
            if not isinstance(schema[key], list) or not schema[key]:
                raise SafeError("官方 schema 组合规则无效")
            for child in schema[key]:
                check_schema(child, root, active)
    if "$ref" in schema:
        check_schema(resolve_ref(schema["$ref"], root), root, active)


def resolve_ref(ref, root):
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise SafeError("官方 schema 引用暂不支持")
    target = root
    try:
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
    except (KeyError, TypeError):
        raise SafeError("官方 schema 引用无效") from None
    return target


def validate_schema(value, schema, root=None, depth=0):
    """按远端 inputSchema 校验；拒绝静默忽略未支持的校验关键字。"""
    if depth == 0:
        validate_value(value)
        validate_value(schema, 502)
        check_schema(schema)
    if depth > 40:
        raise SafeError("参数嵌套过深", 400)
    if isinstance(schema, bool):
        if not schema:
            raise SafeError("参数不符合官方 schema", 400)
        return
    if not isinstance(schema, dict):
        raise SafeError("官方工具 schema 无效")
    root = schema if root is None else root
    if "$ref" in schema:
        validate_schema(value, resolve_ref(schema["$ref"], root), root, depth + 1)
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword not in schema:
            continue
        matches = 0
        for variant in schema[keyword]:
            try:
                validate_schema(value, variant, root, depth + 1)
                matches += 1
            except SafeError as error:
                if error.status != 400:
                    raise
        if ((keyword == "allOf" and matches != len(schema[keyword])) or
                (keyword == "anyOf" and matches == 0) or (keyword == "oneOf" and matches != 1)):
            raise SafeError("参数不符合官方 schema", 400)
    if "not" in schema:
        try:
            validate_schema(value, schema["not"], root, depth + 1)
        except SafeError as error:
            if error.status != 400:
                raise
        else:
            raise SafeError("参数不符合官方 schema", 400)
    checks = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
              "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
              "integer": lambda v: not isinstance(v, bool) and (isinstance(v, int) or
                                                               isinstance(v, float) and v.is_integer()),
              "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
              "null": lambda v: v is None}
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(t in checks and checks[t](value) for t in types):
            raise SafeError("参数类型不符合官方 schema", 400)
    if "enum" in schema and not any(json_equal(value, v) for v in schema["enum"]):
        raise SafeError("参数取值不符合官方 schema", 400)
    if "const" in schema and not json_equal(value, schema["const"]):
        raise SafeError("参数取值不符合官方 schema", 400)
    if isinstance(value, dict):
        if any(k not in value for k in schema.get("required", [])):
            raise SafeError("缺少官方 schema 要求的参数", 400)
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                validate_schema(item, properties[key], root, depth + 1)
            elif schema.get("additionalProperties") is False:
                raise SafeError("包含官方 schema 未允许的参数", 400)
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(item, schema["additionalProperties"], root, depth + 1)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise SafeError("数组长度不符合官方 schema", 400)
        if schema.get("uniqueItems"):
            if len(value) > 1000:
                raise SafeError("唯一性校验数组过大", 400)
            for index, item in enumerate(value):
                if any(json_equal(item, other) for other in value[:index]):
                    raise SafeError("数组包含重复参数", 400)
        for item in value:
            if "items" in schema:
                validate_schema(item, schema["items"], root, depth + 1)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            raise SafeError("文本长度不符合官方 schema", 400)
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise SafeError("文本格式不符合官方 schema", 400)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if (value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf)
                or value <= schema.get("exclusiveMinimum", -math.inf)
                or value >= schema.get("exclusiveMaximum", math.inf)):
            raise SafeError("数值范围不符合官方 schema", 400)
        if "multipleOf" in schema:
            try:
                if Decimal(str(value)) % Decimal(str(schema["multipleOf"])):
                    raise SafeError("数值不符合官方 schema", 400)
            except InvalidOperation:
                raise SafeError("数值校验超过精度限制", 400) from None


class MCPClient:
    def __init__(self):
        self.lock = threading.RLock()
        self.authorization = None
        self.session = None
        self.protocol = "2025-03-26"
        self.schemas = {}
        self.sequence = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.deadline = None

    @contextmanager
    def operation(self):
        started = time.monotonic()
        if not self.lock.acquire(timeout=REQUEST_TIMEOUT):
            raise SafeError("官方 MCP 请求等待超时", 504)
        outer = self.deadline is None
        if outer:
            self.deadline = started + REQUEST_TIMEOUT
        try:
            self.remaining()
            yield
        finally:
            if outer:
                self.deadline = None
            self.lock.release()

    def remaining(self):
        remaining = REQUEST_TIMEOUT if self.deadline is None else self.deadline - time.monotonic()
        if remaining <= 0:
            raise SafeError("官方 MCP 请求超时", 504)
        return remaining

    def read_remote(self, response, size):
        remaining = self.remaining()
        # urllib 的 socket 超时只限制单次读取；每轮另检查整个操作的截止时间。
        try:
            response.fp.raw._sock.settimeout(remaining)
        except AttributeError:
            pass
        chunk = response.read1(size)
        self.remaining()
        return chunk

    def _exchange(self, method, params=None, notification=False):
        with self.operation():
            return self._exchange_once(method, params, notification)

    def _exchange_once(self, method, params=None, notification=False):
        self.sequence += 1
        request_id = self.sequence
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notification:
            message["id"] = request_id
        headers = {"Authorization": self.authorization,
                   "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if method != "initialize":
            headers["MCP-Protocol-Version"] = self.protocol
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(REMOTE, data=json.dumps(message, allow_nan=False).encode("utf-8"), headers=headers, method="POST")
        try:
            with self.opener.open(req, timeout=self.remaining()) as response:
                self.remaining()
                if method == "initialize":
                    sessions = response.headers.get_all("Mcp-Session-Id", [])
                    if len(sessions) > 1 or (sessions and (not sessions[0] or len(sessions[0]) > 1024 or
                                                         any(ord(c) < 33 or ord(c) > 126 for c in sessions[0]))):
                        raise SafeError("官方 MCP 会话标识无效")
                    self.session = sessions[0] if sessions else None
                if notification:
                    return None
                content_type = response.headers.get_content_type()
                if content_type == "text/event-stream":
                    total = 0
                    data_lines = []
                    pending = b""
                    while True:
                        chunk = self.read_remote(response, min(65536, MAX_REMOTE + 1 - total))
                        total += len(chunk)
                        if total > MAX_REMOTE:
                            raise SafeError("官方响应超过大小限制")
                        if not chunk:
                            break
                        pending += chunk
                        lines = pending.split(b"\n")
                        pending = lines.pop()
                        for line in lines:
                            text = line.decode("utf-8").rstrip("\r")
                            if text.startswith("data:"):
                                data_lines.append(text[5:].removeprefix(" "))
                            elif not text and data_lines:
                                packet = decode_json("\n".join(data_lines))
                                data_lines = []
                                if isinstance(packet, dict) and packet.get("id") == request_id:
                                    return self._result(packet, request_id)
                    raise SafeError("官方服务未返回完整响应")
                if content_type != "application/json":
                    raise SafeError("官方响应格式不支持")
                raw = bytearray()
                while True:
                    chunk = self.read_remote(response, min(65536, MAX_REMOTE + 1 - len(raw)))
                    raw.extend(chunk)
                    if len(raw) > MAX_REMOTE:
                        raise SafeError("官方响应超过大小限制")
                    if not chunk:
                        break
                return self._result(decode_json(raw), request_id)
        except urllib.error.HTTPError as error:
            error.close()
            if error.code in (401, 403, 404):
                self.schemas = {}
                self.session = None
            if error.code in (401, 403):
                raise SafeError("官方 MCP 认证失效，请在 WorkBuddy 更新授权", 503) from None
            if error.code == 404:
                raise SafeError("官方 MCP 会话失效，请重新检查连接", 503) from None
            raise SafeError("官方 MCP 请求暂未成功") from None
        except TimeoutError:
            raise SafeError("官方 MCP 请求超时", 504) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise SafeError("官方 MCP 请求超时", 504) from None
            raise SafeError("官方 MCP 连接或响应异常") from None
        except SafeError as error:
            if error.status == 400:
                raise SafeError("官方 MCP 响应格式无效") from None
            raise
        except Exception:
            raise SafeError("官方 MCP 连接或响应异常") from None

    @staticmethod
    def _result(packet, request_id):
        if not isinstance(packet, dict) or packet.get("jsonrpc") != "2.0" or type(packet.get("id")) is not int or packet.get("id") != request_id:
            raise SafeError("官方 MCP 响应无法核验")
        if "error" in packet or "result" not in packet:
            raise SafeError("官方 MCP 未能完成请求")
        return packet["result"]

    def initialize(self):
        with self.operation():
            self.schemas = {}
            self.session = None
            self.authorization = read_authorization()
            result = self._exchange("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                    "clientInfo": {"name": "maiyoushu-local", "version": "1.0"}})
            if not isinstance(result, dict) or result.get("protocolVersion") not in {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}:
                raise SafeError("官方 MCP 协议无法协商")
            self.protocol = result["protocolVersion"]
            self._exchange("notifications/initialized", notification=True)
            schemas = {}
            cursor = None
            for _ in range(20):
                listing = self._exchange("tools/list", {"cursor": cursor} if cursor else {})
                if not isinstance(listing, dict) or not isinstance(listing.get("tools"), list):
                    raise SafeError("官方工具列表无效")
                for tool in listing["tools"]:
                    if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                        raise SafeError("官方工具列表无效")
                    name = tool["name"]
                    if name in ALLOWED_TOOLS:
                        if name in schemas or not isinstance(tool.get("inputSchema"), dict):
                            raise SafeError("官方白名单工具 schema 无效或重复")
                        validate_value(tool["inputSchema"], 502)
                        check_schema(tool["inputSchema"])
                        schemas[name] = tool["inputSchema"]
                cursor = listing.get("nextCursor")
                if cursor is not None and (not isinstance(cursor, str) or not cursor):
                    raise SafeError("官方工具列表游标无效")
                if cursor is None:
                    break
            else:
                raise SafeError("官方工具列表分页过多")
            if schemas.keys() != ALLOWED_TOOLS:
                raise SafeError("官方白名单工具不完整")
            self.schemas = schemas

    def schema(self, name):
        if not isinstance(name, str) or name not in ALLOWED_TOOLS:
            raise SafeError("此工具不在允许列表", 403)
        with self.operation():
            if not self.schemas:
                self.initialize()
            return copy.deepcopy(self.schemas[name])

    def call(self, name, arguments):
        if not isinstance(name, str) or name not in ALLOWED_TOOLS:
            raise SafeError("此工具不在允许列表", 403)
        if not isinstance(arguments, dict):
            raise SafeError("arguments 必须是 JSON 对象", 400)
        validate_value(arguments)
        with self.operation():
            schema = self.schema(name)
            validate_schema(arguments, schema)
            result = self._exchange("tools/call", {"name": name, "arguments": arguments})
            if not isinstance(result, dict) or ("isError" in result and type(result["isError"]) is not bool):
                raise SafeError("官方工具响应格式无效")
            if result.get("isError"):
                raise SafeError("官方工具未能完成查询")
            return result

    def public_bytes(self, value):
        validate_value(value, 502)
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        # 检查 JSON 转义嵌套的回显，避免上游把凭证夹在字符串字段中。
        if self.authorization:
            secrets = (self.authorization, self.authorization[7:])
            pending = [value]
            while pending:
                item = pending.pop()
                if isinstance(item, dict):
                    pending.extend(item.keys())
                    pending.extend(item.values())
                elif isinstance(item, list):
                    pending.extend(item)
                elif isinstance(item, str):
                    for _ in range(8):
                        if any(secret and secret in item for secret in secrets):
                            raise SafeError("响应包含受保护信息，已阻止返回")
                        decoded = re.sub(r'\\u([0-9a-fA-F]{4})|\\(["\\/])',
                                         lambda m: chr(int(m[1], 16)) if m[1] else m[2], item)
                        if decoded == item:
                            break
                        item = decoded
        return raw


class Handler(BaseHTTPRequestHandler):
    server_version = "MaiYouShuLocal/1.0"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        pass

    def log_error(self, format, *args):
        pass

    def _send(self, status, raw, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _json(self, status, value):
        self._send(status, self.server.mcp.public_bytes(value))

    def _security(self):
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0] not in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}:
            raise SafeError("请求主机不被允许", 403)
        origins = self.headers.get_all("Origin", [])
        if origins and (len(origins) != 1 or origins[0] != "http://" + hosts[0]):
            raise SafeError("请求来源不被允许", 403)
        sites = self.headers.get_all("Sec-Fetch-Site", [])
        if sites and (len(sites) != 1 or sites[0] not in {"none", "same-origin"}):
            raise SafeError("请求来源不被允许", 403)
        if self.headers.get_all("Transfer-Encoding", []):
            raise SafeError("不支持此请求编码", 400)

    def _dispatch(self):
        self._security()
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise SafeError("请求路径无效", 400)
        path = parsed.path
        if path.startswith("/api/"):
            supplied = self.headers.get_all(API_TOKEN_HEADER, [])
            matched = False
            if len(supplied) == 1:
                try:
                    matched = hmac.compare_digest(supplied[0].encode("utf-8"), API_TOKEN.encode("ascii"))
                except UnicodeEncodeError:
                    matched = False
            if not matched:
                raise SafeError("访问令牌无效或已过期，请刷新页面", 403)
        if self.command == "GET":
            if path == "/api/status":
                try:
                    self.server.mcp.initialize()
                except SafeError as error:
                    self._json(200, {"ok": True, "connected": False, "message": error.message})
                else:
                    self._json(200, {"ok": True, "connected": True, "message": "官方 MCP 初始化及白名单工具校验成功"})
                return
            if path == "/api/schema":
                try:
                    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True,
                                                  max_num_fields=4)
                except ValueError:
                    raise SafeError("工具查询参数无效", 400) from None
                if set(query) != {"name"} or len(query["name"]) != 1:
                    raise SafeError("请指定一个工具名称", 400)
                self._json(200, {"ok": True, "schema": self.server.mcp.schema(query["name"][0])})
                return
            if path in STATIC_FILES and not parsed.query:
                file_path = ROOT / STATIC_FILES[path]
                if file_path.is_symlink() or file_path.resolve().parent != ROOT or not file_path.is_file():
                    raise SafeError("文件不存在", 404)
                mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                        ".css": "text/css; charset=utf-8"}[file_path.suffix]
                raw = file_path.read_bytes()
                if file_path.suffix == ".html":
                    # 把本次运行的访问令牌注入页面 meta，磁盘文件与分发包始终不含令牌。
                    raw = raw.replace(b'<meta name="maiyoushu-token" content="">',
                                      b'<meta name="maiyoushu-token" content="' + API_TOKEN.encode("ascii") + b'">', 1)
                self._send(200, raw, mime)
                return
            raise SafeError("接口或文件不存在", 404)
        if self.command == "POST" and path == "/api/call" and not parsed.query:
            if len(self.headers.get_all("Content-Type", [])) != 1 or self.headers.get_content_type() != "application/json":
                raise SafeError("请求必须使用 application/json", 415)
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
                raise SafeError("请求长度无效", 411)
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY:
                raise SafeError("请求体过大或为空", 413)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise SafeError("请求体不完整", 400)
            try:
                payload = decode_json(raw.decode("utf-8"))
            except (UnicodeError, ValueError, RecursionError):
                raise SafeError("请求不是有效的 JSON", 400) from None
            if not isinstance(payload, dict) or set(payload) != {"name", "arguments"}:
                raise SafeError("请求必须包含 name 和 arguments", 400)
            result = self.server.mcp.call(payload["name"], payload["arguments"])
            self._json(200, {"ok": True, "result": result})
            return
        raise SafeError("请求方法不被允许", 405)

    def _handle(self):
        try:
            self._dispatch()
        except SafeError as error:
            self._send(error.status, json.dumps({"ok": False, "message": error.message}, ensure_ascii=False).encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            try:
                self._send(500, '{"ok":false,"message":"本地服务暂时无法完成请求"}'.encode("utf-8"))
            except OSError:
                pass

    do_GET = _handle
    do_POST = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_PATCH = _handle

    def send_error(self, code, message=None, explain=None):
        self._send(code, '{"ok":false,"message":"HTTP 请求无效"}'.encode("utf-8"))


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__((HOST, PORT), Handler)
        self.mcp = MCPClient()
        self.workers = threading.BoundedSemaphore(MAX_WORKERS)

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.workers.release()

    def handle_error(self, request, client_address):
        pass


def main():
    try:
        server = LocalServer()
    except OSError:
        print("本地服务启动失败：127.0.0.1:8765 无法绑定", flush=True)
        return 1
    print("本地服务已启动：http://127.0.0.1:8765", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
