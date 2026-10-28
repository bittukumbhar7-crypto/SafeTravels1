import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module
from app import ALL_SEATS, DEMO_OCCUPIED_SEATS, app
from werkzeug.security import generate_password_hash


class FakeCursor:
    def __init__(self, users):
        self.users = users
        self.user = None

    def execute(self, query, values=None):
        if query.startswith("SELECT * FROM users"):
            self.user = self.users.get(values[0])
        elif query.startswith("SELECT is_admin FROM users"):
            self.user = next((user for user in self.users.values() if user["user_id"] == values[0]), None)

    def fetchone(self):
        return self.user

    def close(self):
        pass


def test_expected_routes_are_registered():
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert {"/searchflights", "/seat_selection", "/confirm_pay", "/mybookings", "/api/flights/<int:flight_id>"} <= rules


def test_protected_pages_redirect_without_a_session():
    client = app.test_client()
    assert client.get("/searchflights").status_code == 302
    assert client.get("/api/flights/1").status_code == 302


def test_demo_seat_layout_has_unique_valid_seats():
    assert len(ALL_SEATS) == 30
    assert DEMO_OCCUPIED_SEATS <= ALL_SEATS


def test_single_login_routes_customer_and_admin_by_database_role(monkeypatch):
    users = {
        "customer@example.test": {"user_id": 1, "full_name": "Customer User", "password": generate_password_hash("correct-password"), "is_admin": 0},
        "admin@example.test": {"user_id": 2, "full_name": "Admin User", "password": generate_password_hash("correct-password"), "is_admin": 1},
    }
    monkeypatch.setattr(app_module, "db_cursor", lambda: FakeCursor(users))
    client = app.test_client()

    customer = client.post("/login", data={"txtemail": "customer@example.test", "txtpassword": "correct-password"})
    assert customer.status_code == 302 and customer.headers["Location"].endswith("/")

    admin = client.post("/login", data={"txtemail": "admin@example.test", "txtpassword": "correct-password"})
    assert admin.status_code == 302 and admin.headers["Location"].endswith("/dashboard")


def test_login_rejects_bad_password_and_denies_customer_dashboard(monkeypatch):
    users = {"customer@example.test": {"user_id": 1, "full_name": "Customer User", "password": generate_password_hash("correct-password"), "is_admin": None}}
    monkeypatch.setattr(app_module, "db_cursor", lambda: FakeCursor(users))
    client = app.test_client()

    bad_password = client.post("/login", data={"txtemail": "customer@example.test", "txtpassword": "wrong"})
    assert b"Email or password is incorrect" in bad_password.data

    with client.session_transaction() as session:
        session["user_id"] = 1
    assert client.get("/dashboard").status_code == 403


def test_customer_cannot_open_any_admin_management_route(monkeypatch):
    users = {"customer@example.test": {"user_id": 1, "full_name": "Customer User", "password": generate_password_hash("correct-password"), "is_admin": 0}}
    monkeypatch.setattr(app_module, "db_cursor", lambda: FakeCursor(users))
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["csrf_token"] = "test-token"
    for url in ("/flights", "/flights/add", "/flights/1/edit", "/aircraft", "/aircraft/add", "/aircraft/1/edit", "/bookings", "/bookings/1"):
        assert client.get(url).status_code == 403
    assert client.post("/flights/1/status", data={"csrf_token": "test-token", "status": "Active"}).status_code == 403


class FakePriceModel:
    def predict(self, data):
        assert list(data.columns) == ["airline"]
        return [6543.21]


def test_price_prediction_uses_only_airline_and_handles_invalid_input(monkeypatch):
    monkeypatch.setattr(app_module, "price_model", FakePriceModel())
    monkeypatch.setattr(app_module, "price_model_metrics", {"mae": 1, "rmse": 2, "r2": 0, "limitation": "airline-only"})
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
    response = client.post("/predict-price", data={"airline": "Air India"})
    assert response.status_code == 200
    assert b"6,543.21" in response.data
    invalid = client.post("/predict-price", data={"airline": ""})
    assert invalid.status_code == 400


def test_price_prediction_reports_missing_model(monkeypatch):
    monkeypatch.setattr(app_module, "price_model", None)
    monkeypatch.setattr(app_module, "price_model_error", "Model file is missing.")
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
    response = client.post("/predict-price", data={"airline": "Air India"})
    assert response.status_code == 503
    assert b"Model file is missing" in response.data
