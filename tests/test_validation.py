from validation import validate_proxy, normalize_proxy, is_valid_ipv4, is_valid_ip_or_host

# 已存在的规则（用于重复检测）
EXISTING = [
    {"name": "ssh", "type": "tcp", "localIP": "127.0.0.1", "localPort": 22, "remotePort": 6000},
    {"name": "web", "type": "http", "localIP": "127.0.0.1", "localPort": 80, "customDomains": ["a.example.com"]},
]


def test_valid_tcp():
    p = {"name": "rdp", "type": "tcp", "localIP": "127.0.0.1", "localPort": 3389, "remotePort": 7000}
    assert validate_proxy(p, EXISTING) == []


def test_missing_name():
    p = {"name": "", "type": "tcp", "localIP": "127.0.0.1", "localPort": 22, "remotePort": 6001}
    errs = validate_proxy(p, EXISTING)
    assert any(e["field"] == "name" for e in errs)


def test_illegal_name_chars():
    p = {"name": "bad name!", "type": "tcp", "localIP": "127.0.0.1", "localPort": 22, "remotePort": 6001}
    errs = validate_proxy(p, EXISTING)
    assert any(e["field"] == "name" for e in errs)


def test_duplicate_name():
    p = {"name": "ssh", "type": "tcp", "localIP": "127.0.0.1", "localPort": 22, "remotePort": 6001}
    errs = validate_proxy(p, EXISTING)
    assert any("已存在" in e["msg"] for e in errs)


def test_bad_local_port():
    p = {"name": "x", "type": "tcp", "localIP": "127.0.0.1", "localPort": 70000, "remotePort": 6001}
    errs = validate_proxy(p, EXISTING)
    assert any(e["field"] == "localPort" for e in errs)


def test_duplicate_remote_port():
    p = {"name": "x", "type": "tcp", "localIP": "127.0.0.1", "localPort": 22, "remotePort": 6000}
    errs = validate_proxy(p, EXISTING)
    assert any(e["field"] == "remotePort" for e in errs)


def test_tcp_missing_remote_port():
    p = {"name": "x", "type": "tcp", "localIP": "127.0.0.1", "localPort": 22}
    errs = validate_proxy(p, EXISTING)
    assert any(e["field"] == "remotePort" for e in errs)


def test_http_missing_domain():
    p = {"name": "x", "type": "http", "localIP": "127.0.0.1", "localPort": 8080}
    errs = validate_proxy(p, EXISTING)
    assert any(e["field"] == "customDomains" for e in errs)


def test_http_valid_with_subdomain():
    p = {"name": "x", "type": "https", "localIP": "127.0.0.1", "localPort": 8080, "subdomain": "myapp"}
    assert validate_proxy(p, EXISTING) == []


def test_http_duplicate_domain():
    p = {"name": "x", "type": "http", "localIP": "127.0.0.1", "localPort": 8081, "customDomains": ["a.example.com"]}
    errs = validate_proxy(p, EXISTING)
    assert any(e["field"] == "customDomains" for e in errs)


def test_stcp_missing_secret():
    p = {"name": "x", "type": "stcp", "localIP": "127.0.0.1", "localPort": 22}
    errs = validate_proxy(p, EXISTING)
    assert any(e["field"] == "secretKey" for e in errs)


def test_stcp_visitor_missing_servername():
    p = {"name": "x", "type": "stcp", "localIP": "127.0.0.1", "localPort": 22,
         "secretKey": "abc", "role": "visitor"}
    errs = validate_proxy(p, EXISTING)
    assert any(e["field"] == "serverName" for e in errs)


def test_unsupported_type():
    p = {"name": "x", "type": "ftp", "localIP": "127.0.0.1", "localPort": 21}
    errs = validate_proxy(p, EXISTING)
    assert any(e["field"] == "type" for e in errs)


def test_edit_excludes_self():
    # 编辑 ssh 本身时，调用方会先排除自身；这里模拟该行为
    others = [p for p in EXISTING if p["name"] != "ssh"]
    p = {"name": "ssh", "type": "tcp", "localIP": "127.0.0.1", "localPort": 22, "remotePort": 6000}
    errs = validate_proxy(p, others)
    assert not any("已存在" in e["msg"] for e in errs)


def test_ip_helpers():
    assert is_valid_ipv4("192.168.1.1")
    assert not is_valid_ipv4("256.1.1.1")
    assert is_valid_ip_or_host("localhost")
    assert is_valid_ip_or_host("host.local")


def test_normalize_drops_empty():
    raw = {"name": "ssh", "type": "tcp", "localIP": "127.0.0.1", "localPort": "22",
           "remotePort": "", "useEncryption": "true"}
    out = normalize_proxy(raw)
    assert out["localPort"] == 22
    assert "remotePort" not in out
    # frp v0.70.1 v1 schema：useEncryption / useCompression 在 transport 子对象
    assert out["transport"]["useEncryption"] is True
    assert out["name"] == "ssh"


def test_normalize_transport_for_compression():
    raw = {"name": "x", "type": "tcp", "localIP": "1.2.3.4", "localPort": 22,
           "remotePort": 6000, "useCompression": "1", "useEncryption": "yes"}
    out = normalize_proxy(raw)
    assert out["transport"]["useCompression"] is True
    assert out["transport"]["useEncryption"] is True
    # 不应再出现在顶层
    assert "useCompression" not in out
    assert "useEncryption" not in out
