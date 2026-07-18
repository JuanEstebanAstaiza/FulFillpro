"""Smoke test against a running FulfillPro API."""
import json
from pathlib import Path
from urllib import request

base = "http://127.0.0.1:8000"


def post_json(path, data, token=None):
    req = request.Request(base + path, data=json.dumps(data).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with request.urlopen(req) as r:
        return json.loads(r.read().decode())


def get_json(path, token):
    req = request.Request(base + path)
    req.add_header("Authorization", f"Bearer {token}")
    with request.urlopen(req) as r:
        return json.loads(r.read().decode())


def main():
    # Portal empresas (no platform admin)
    login = post_json(
        "/api/auth/login",
        {"email": "empresa@demo.com", "password": "DemoEmpresa2026!"},
    )
    token = login["access_token"]
    print("login company ok", "needs_consent", login.get("needs_consent"))

    # Firmar términos si aplica
    import urllib.error

    req = request.Request(base + "/api/legal/pending")
    req.add_header("Authorization", f"Bearer {token}")
    with request.urlopen(req) as r:
        pending = json.loads(r.read().decode())
    if pending.get("required") and pending.get("document"):
        post_json(
            "/api/legal/sign",
            {
                "document_id": pending["document"]["id"],
                "signature_name": "Admin Empresa Demo",
                "accepted": True,
            },
            token,
        )
        print("legal signed")

    dash = get_json("/api/licenses/dashboard", token)
    print("dashboard ok", dash.get("ok"), "uses", (dash.get("license") or {}).get("uses"))

    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    sample = Path("/app/samples/ordenes_muestra.xlsx")
    if not sample.exists():
        sample = Path("samples/ordenes_muestra.xlsx")
    file_data = sample.read_bytes()
    fields = {}
    body = b""
    for k, v in fields.items():
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
        ).encode()
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="ordenes_muestra.xlsx"\r\n'
        f"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
    ).encode()
    body += file_data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = request.Request(base + "/api/process", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with request.urlopen(req) as r:
        out = r.read()
        print("process bytes", len(out), "order", r.headers.get("X-Order-Id"))

    out_path = Path("/app/storage/test_out.xlsx")
    try:
        out_path.write_bytes(out)
    except Exception:
        Path("storage/test_out.xlsx").write_bytes(out)

    orders = get_json("/api/orders", token)
    print("orders", orders["total"], orders["items"][0]["status"] if orders["items"] else None)

    # Platform admin (login oculto)
    plat = post_json(
        "/api/auth/login/platform",
        {"email": "admin@fulfillpro.com", "password": "AdminFulfillPro2026!"},
    )
    mon = get_json("/api/admin/monitoring/overview", plat["access_token"])
    print(
        "platform mon",
        {
            k: mon[k]
            for k in ["total_users", "active_licenses", "orders_today"]
        },
    )
    print("OK")


if __name__ == "__main__":
    main()
