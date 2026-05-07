from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


BASE_DIR = Path(__file__).resolve().parent
FIXTURE_PATH = BASE_DIR / "customer_records.json"
HOST = os.getenv("BLISSFUL_MOCK_CRM_HOST", "127.0.0.1")
PORT = int(os.getenv("BLISSFUL_MOCK_CRM_PORT", "8091"))
RECOMMENDATION_PROXY_BASE_URL = os.getenv(
    "BLISSFUL_RECOMMENDATION_BASE_URL",
    "https://ces-plant-demo-mock-endpoint-zj7deoy6hq-uc.a.run.app",
)


class CustomerStore:
    def __init__(self, fixture_path: Path) -> None:
        with fixture_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        customers = payload.get("customers")
        if not isinstance(customers, list):
            raise RuntimeError("customer_records.json must contain a 'customers' list")

        self._customers_by_id: dict[str, dict] = {}
        self._profile_key_to_customer_id: dict[str, str] = {}

        for customer in customers:
            customer_id = customer.get("customer_id")
            profile_key = customer.get("profile_key")
            if not isinstance(customer_id, str) or not customer_id:
                raise RuntimeError("Each customer must define a non-empty customer_id")
            if not isinstance(profile_key, str) or not profile_key:
                raise RuntimeError("Each customer must define a non-empty profile_key")

            self._customers_by_id[customer_id] = customer
            self._profile_key_to_customer_id[profile_key] = customer_id

    def resolve_customer(self, *, customer_id: str | None = None, profile_key: str | None = None, account_number: str | None = None, email: str | None = None, phone: str | None = None) -> dict | None:
        if customer_id:
            customer = self._customers_by_id.get(customer_id)
            return deepcopy(customer) if customer else None

        if profile_key:
            resolved_id = self._profile_key_to_customer_id.get(profile_key)
            if resolved_id:
                return deepcopy(self._customers_by_id[resolved_id])

        for customer in self._customers_by_id.values():
            if account_number and customer.get("external_ids", {}).get("account_number") == account_number:
                return deepcopy(customer)
            contact = customer.get("contact", {})
            if email and contact.get("email") == email:
                return deepcopy(customer)
            if phone and contact.get("phone") == phone:
                return deepcopy(customer)

        return None

    def get_customer(self, customer_id: str) -> dict | None:
        customer = self._customers_by_id.get(customer_id)
        return deepcopy(customer) if customer else None

    def get_cart(self, customer_id: str) -> dict | None:
        customer = self._customers_by_id.get(customer_id)
        if customer is None:
            return None
        return deepcopy(customer.get("active_cart", {}))

    def get_appointments(self, customer_id: str) -> dict | None:
        customer = self._customers_by_id.get(customer_id)
        if customer is None:
            return None
        return deepcopy(customer.get("appointments", {}))

    def get_purchase_history(self, customer_id: str) -> list[dict] | None:
        customer = self._customers_by_id.get(customer_id)
        if customer is None:
            return None
        return deepcopy(customer.get("purchase_history", []))

    def get_loyalty(self, customer_id: str) -> dict | None:
        customer = self._customers_by_id.get(customer_id)
        if customer is None:
            return None
        metrics = customer.get("metrics", {})
        return {
            "customer_id": customer_id,
            "loyalty_points": metrics.get("loyalty_points", 0),
            "tenure_years": metrics.get("tenure_years", 0),
            "status": customer.get("status", "unknown"),
        }

    def add_cart_item(self, customer_id: str, item: dict) -> dict | None:
        customer = self._customers_by_id.get(customer_id)
        if customer is None:
            return None

        cart = customer.setdefault("active_cart", {"items": [], "subtotal": 0.0, "estimated_tax": 0.0, "total": 0.0})
        items = cart.setdefault("items", [])
        product_id = item.get("product_id")

        for existing_item in items:
            if existing_item.get("product_id") == product_id:
                existing_item["quantity"] = int(existing_item.get("quantity", 0)) + int(item.get("quantity", 1))
                self._recalculate_cart(cart)
                return deepcopy(cart)

        items.append(
            {
                "product_id": product_id,
                "name": item.get("name", product_id),
                "quantity": int(item.get("quantity", 1)),
                "unit_price": float(item.get("unit_price", 0.0)),
                "category": item.get("category", "general"),
            }
        )
        self._recalculate_cart(cart)
        return deepcopy(cart)

    def update_cart_item(self, customer_id: str, product_id: str, quantity: int) -> dict | None:
        customer = self._customers_by_id.get(customer_id)
        if customer is None:
            return None

        cart = customer.setdefault("active_cart", {"items": [], "subtotal": 0.0, "estimated_tax": 0.0, "total": 0.0})
        items = cart.setdefault("items", [])
        for existing_item in list(items):
            if existing_item.get("product_id") != product_id:
                continue
            if quantity <= 0:
                items.remove(existing_item)
            else:
                existing_item["quantity"] = quantity
            self._recalculate_cart(cart)
            return deepcopy(cart)
        return deepcopy(cart)

    @staticmethod
    def _recalculate_cart(cart: dict) -> None:
        subtotal = 0.0
        for item in cart.get("items", []):
            subtotal += float(item.get("unit_price", 0.0)) * int(item.get("quantity", 0))

        cart["subtotal"] = round(subtotal, 2)
        cart["estimated_tax"] = round(subtotal * 0.0825, 2)
        cart["total"] = round(cart["subtotal"] + cart["estimated_tax"], 2)
        cart["last_updated"] = datetime.now(timezone.utc).isoformat()


STORE = CustomerStore(FIXTURE_PATH)


class MockCrmHandler(BaseHTTPRequestHandler):
    server_version = "BlissfulMockCrm/0.1"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path

        if path == "/health":
            self._send_json(200, {"status": "ok", "service": "mock-crm"})
            return

        if path == "/customers:resolve":
            customer = STORE.resolve_customer(
                customer_id=self._first_value(query, "customer_id"),
                profile_key=self._first_value(query, "profile_key"),
                account_number=self._first_value(query, "account_number"),
                email=self._first_value(query, "email"),
                phone=self._first_value(query, "phone"),
            )
            if customer is None:
                self._send_json(404, {"error": "Customer not found"})
                return
            self._send_json(200, {"results": customer})
            return

        if path == "/get_cart_information":
            customer = self._resolve_customer_from_query(query)
            if customer is None:
                self._send_json(404, {"error": "Customer not found"})
                return
            cart = customer.get("active_cart", {})
            self._send_json(200, {"results": {"items": cart.get("items", []), "subtotal": cart.get("subtotal", 0.0)}})
            return

        if path == "/get_product_recommendations":
            self._proxy_get(RECOMMENDATION_PROXY_BASE_URL + "/get_product_recommendations")
            return

        customer_response = self._handle_customer_resource(path)
        if customer_response is not None:
            status_code, payload = customer_response
            self._send_json(status_code, payload)
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        payload = self._read_json_body()

        if path == "/get_landscaping_quote":
            target_url = RECOMMENDATION_PROXY_BASE_URL + "/get_landscaping_quote"
            self._proxy_post(target_url, payload)
            return

        if path.startswith("/customers/") and path.endswith("/cart/items"):
            customer_id = path.split("/")[2]
            cart = STORE.add_cart_item(customer_id, payload)
            if cart is None:
                self._send_json(404, {"error": "Customer not found"})
                return
            self._send_json(200, {"results": cart})
            return

        self._send_json(404, {"error": "Not found"})

    def do_PATCH(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        payload = self._read_json_body()

        if path.startswith("/customers/") and "/cart/items/" in path:
            segments = path.strip("/").split("/")
            customer_id = segments[1]
            product_id = segments[-1]
            cart = STORE.update_cart_item(customer_id, product_id, int(payload.get("quantity", 0)))
            if cart is None:
                self._send_json(404, {"error": "Customer not found"})
                return
            self._send_json(200, {"results": cart})
            return

        self._send_json(404, {"error": "Not found"})

    def _handle_customer_resource(self, path: str) -> tuple[int, dict] | None:
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 2 or segments[0] != "customers":
            return None

        customer_id = segments[1]
        customer = STORE.get_customer(customer_id)
        if customer is None:
            return 404, {"error": "Customer not found"}

        if len(segments) == 2:
            return 200, {"results": customer}
        if len(segments) == 3 and segments[2] == "cart":
            return 200, {"results": STORE.get_cart(customer_id)}
        if len(segments) == 3 and segments[2] == "appointments":
            return 200, {"results": STORE.get_appointments(customer_id)}
        if len(segments) == 3 and segments[2] == "purchase-history":
            return 200, {"results": STORE.get_purchase_history(customer_id)}
        if len(segments) == 3 and segments[2] == "loyalty":
            return 200, {"results": STORE.get_loyalty(customer_id)}
        return None

    def _resolve_customer_from_query(self, query: dict[str, list[str]]) -> dict | None:
        return STORE.resolve_customer(
            customer_id=self._first_value(query, "customer_id"),
            profile_key=self._first_value(query, "profile_key") or self._first_value(query, "customer_profile"),
            account_number=self._first_value(query, "account_number"),
        )

    @staticmethod
    def _first_value(query: dict[str, list[str]], key: str) -> str | None:
        values = query.get(key)
        if not values:
            return None
        return values[0]

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        body = self.rfile.read(content_length)
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def _proxy_get(self, url: str) -> None:
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read()
                self._send_raw_json(response.status, payload)
        except urllib.error.HTTPError as exc:
            self._send_raw_json(exc.code, exc.read())

    def _proxy_post(self, url: str, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read()
                self._send_raw_json(response.status, body)
        except urllib.error.HTTPError as exc:
            self._send_raw_json(exc.code, exc.read())

    def _send_json(self, status_code: int, payload: dict) -> None:
        self._send_raw_json(status_code, json.dumps(payload).encode("utf-8"))

    def _send_raw_json(self, status_code: int, payload: bytes) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def run() -> None:
    server = ThreadingHTTPServer((HOST, PORT), MockCrmHandler)
    print(f"Mock CRM listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run()