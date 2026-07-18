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
    login = post_json(
        "/api/auth/login",
        {"email": "admin@fulfillpro.com", "password": "AdminFulfillPro2026!"},
    )
    token = login["access_token"]
    print("login ok")

    act = post_json(
        "/api/licenses/activate",
        {
            "code": "DEMO-TRIAL",
            "device_id": "PC-TEST-001",
            "device_name": "Dev Laptop",
            "device_soft": "soft1",
        },
        token,
    )
    print(
        "activate",
        act["device_status"],
        "uses",
        act["license"]["uses"],
        "devices",
        act["license"]["devices_count"],
    )

    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    sample = Path("/app/samples/ordenes_muestra.xlsx")
    if not sample.exists():
        sample = Path("samples/ordenes_muestra.xlsx")
    file_data = sample.read_bytes()
    fields = {
        "license_code": "DEMO-TRIAL",
        "device_id": "PC-TEST-001",
        "device_soft": "soft1",
    }
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
    mon = get_json("/api/admin/monitoring/overview", token)
    print(
        "mon",
        {
            k: mon[k]
            for k in ["total_users", "active_licenses", "orders_today", "active_devices"]
        },
    )
    print("OK")


if __name__ == "__main__":
    main()
