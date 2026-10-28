"""SafeTravels Flask application. Apply migrations/001_booking_integrity.sql first."""
import os
import re
import secrets
import hmac
import json
from datetime import date, datetime
from functools import wraps
from pathlib import Path

import joblib
import pandas as pd
import requests
from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_mysqldb import MySQL
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
app.config.update(MYSQL_HOST=os.getenv("MYSQL_HOST"), MYSQL_USER=os.getenv("MYSQL_USER"),
                  MYSQL_PASSWORD=os.getenv("MYSQL_PASSWORD"), MYSQL_DB=os.getenv("MYSQL_DB", "safe_travels"),
                  MYSQL_CURSORCLASS="DictCursor")
mysql = MySQL(app)

# The pipeline is loaded once, not retrained during a customer request.
MODEL_PATH = Path(__file__).parent / "models" / "price_model.joblib"
METRICS_PATH = Path(__file__).parent / "models" / "metrics.json"
price_model = None
price_model_error = None
price_model_metrics = {}
try:
    price_model = joblib.load(MODEL_PATH)
    if METRICS_PATH.exists():
        price_model_metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError, ImportError, AttributeError) as error:
    price_model_error = "The price estimate model is unavailable. Train it with python train_price_model.py."
    app.logger.warning("Price model was not loaded: %s", type(error).__name__)

city_codes = {"mumbai": "BOM", "delhi": "DEL", "bangalore": "BLR", "bengaluru": "BLR", "hyderabad": "HYD", "chennai": "MAA", "kolkata": "CCU", "ahmedabad": "AMD", "lucknow": "LKO", "pune": "PNQ", "jaipur": "JAI", "goa": "GOI"}
airport_names = {"BOM": "Mumbai", "DEL": "Delhi", "BLR": "Bangalore", "HYD": "Hyderabad", "MAA": "Chennai", "CCU": "Kolkata", "AMD": "Ahmedabad", "LKO": "Lucknow", "PNQ": "Pune", "JAI": "Jaipur", "GOI": "Goa"}
DEMO_OCCUPIED_SEATS = {"1B", "1F", "2D", "3E", "4A", "5C"}
ALL_SEATS = {f"{row}{column}" for row in range(1, 6) for column in "ABCDEF"}


def format_duration(minutes):
    minutes = int(minutes or 0)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def get_airport_name(code):
    return airport_names.get(code, code)


def prediction_page(**values):
    """Render the estimate panel with the honest saved-model limitation."""
    values.setdefault("model_available", price_model is not None)
    values.setdefault("model_message", price_model_error or price_model_metrics.get("limitation"))
    values.setdefault("model_metrics", price_model_metrics)
    return render_template("customer/search_flights.html", **values)


def is_admin_account(role_value):
    """Accept MySQL's common integer/boolean role representations safely."""
    if isinstance(role_value, bool):
        return role_value
    if isinstance(role_value, int):
        return role_value == 1
    return str(role_value).strip().lower() in {"1", "true", "yes"}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("log"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Require the explicit database role added by the migration."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("log"))
        relationship = db_cursor()
        relationship.execute("SELECT is_admin FROM users WHERE user_id=%s", (session["user_id"],))
        user = relationship.fetchone()
        relationship.close()
        if not user or not is_admin_account(user.get("is_admin")):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def db_cursor():
    return mysql.connection.cursor()


def csrf_token():
    """Return one session-bound token for admin state-changing forms."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def valid_csrf_token():
    submitted = request.form.get("csrf_token", "")
    saved = session.get("csrf_token", "")
    return bool(saved and submitted and hmac.compare_digest(saved, submitted))


def admin_schema_ready():
    """Allow clear migration guidance instead of a MySQL table-not-found error."""
    relationship = db_cursor()
    relationship.execute("SHOW TABLES LIKE 'scheduled_flights'")
    schedules = relationship.fetchone()
    relationship.execute("SHOW TABLES LIKE 'aircraft'")
    aircraft = relationship.fetchone()
    relationship.close()
    return bool(schedules and aircraft)


def user_flight(flight_id):
    relationship = db_cursor()
    relationship.execute("""SELECT fr.*, fs.travellers, fs.source_city, fs.destination_city
        FROM flight_results fr JOIN flight_searches fs ON fr.search_id=fs.search_id
        WHERE fr.flight_id=%s AND fs.user_id=%s""", (flight_id, session["user_id"]))
    flight = relationship.fetchone()
    relationship.close()
    return flight


def occupied_seats(flight_id):
    relationship = db_cursor()
    relationship.execute("SELECT seat_number FROM passengers WHERE flight_id=%s", (flight_id,))
    booked = {row["seat_number"] for row in relationship.fetchall()}
    relationship.close()
    return DEMO_OCCUPIED_SEATS | booked


def validate_seats(flight_id, seats, travellers):
    seats = [seat.upper().strip() for seat in seats if seat.strip()]
    if len(seats) != travellers:
        return None, f"Please select exactly {travellers} seat(s)."
    if len(set(seats)) != len(seats):
        return None, "Each traveller needs a different seat."
    if not set(seats).issubset(ALL_SEATS):
        return None, "One or more selected seats are invalid."
    if set(seats) & occupied_seats(flight_id):
        return None, "One or more seats are occupied. Please select again."
    return seats, None


def parse_api_flight(raw):
    outbound = raw.get("outbound", {})
    segments = outbound.get("segments", [])
    if not segments:
        return None
    first, last = segments[0], segments[-1]
    route = " → ".join([segment.get("departure_airport", "Unknown") for segment in segments] + [last.get("arrival_airport", "Unknown")])
    return {"airline": outbound.get("carrier", "Unknown"), "flight_number": first.get("flight_number", "Unknown"), "route": route,
            "departure_datetime": first.get("departure_time_local"), "arrival_datetime": last.get("arrival_time_local"),
            "duration_minutes": outbound.get("duration_minutes", 0), "stops": len(segments) - 1, "layover": None,
            "travel_class": raw.get("cabin_class", "economy"), "price": raw.get("price", {}).get("amount"),
            "price_status": raw.get("price", {}).get("status", "")}


@app.context_processor
def helpers():
    return {"format_duration": format_duration, "get_airport_name": get_airport_name, "csrf_token": csrf_token}


@app.route("/")
def home():
    return render_template("customer/home.html")


@app.route("/login", methods=["GET", "POST"])
def log():
    if request.method == "POST":
        email, password = request.form.get("txtemail", "").strip().lower(), request.form.get("txtpassword", "")
        relationship = db_cursor()
        relationship.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = relationship.fetchone()
        if not user:
            relationship.close(); flash("Email or password is incorrect.", "danger"); return render_template("customer/login.html")
        stored = user.get("password", "")
        valid = check_password_hash(stored, password) if stored.startswith(("pbkdf2:", "scrypt:")) else stored == password
        if not valid:
            relationship.close(); flash("Email or password is incorrect.", "danger"); return render_template("customer/login.html")
        account_is_admin = is_admin_account(user.get("is_admin"))
        if not stored.startswith(("pbkdf2:", "scrypt:")):
            relationship.execute("UPDATE users SET password=%s WHERE user_id=%s", (generate_password_hash(password), user["user_id"]))
            mysql.connection.commit()
        relationship.close()
        session.clear(); session["user_id"] = user["user_id"]; session["name"] = user.get("full_name", "Traveller").split()[0]
        return redirect(url_for("dash") if account_is_admin else url_for("home"))
    return render_template("customer/login.html")


@app.route("/logout")
def logout():
    session.clear(); flash("You have been logged out.", "success")
    return redirect(url_for("home"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name, email = request.form.get("txtname", "").strip(), request.form.get("txtemail", "").strip().lower()
        mobile, password, confirmation = request.form.get("txtmob", "").strip(), request.form.get("txtpassword", ""), request.form.get("txtconfirm", "")
        if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or not mobile or len(password) < 8:
            flash("Enter a name, valid email/mobile, and an 8-character password.", "danger")
        elif password != confirmation:
            flash("Passwords do not match.", "danger")
        else:
            relationship = db_cursor(); relationship.execute("SELECT user_id FROM users WHERE email=%s OR mobile=%s", (email, mobile))
            if relationship.fetchone():
                relationship.close(); flash("That email or mobile is already registered.", "danger")
            else:
                relationship.execute("INSERT INTO users(full_name,email,mobile,password) VALUES (%s,%s,%s,%s)", (name, email, mobile, generate_password_hash(password)))
                mysql.connection.commit(); relationship.close(); flash("Account created. Please log in.", "success")
                return redirect(url_for("log"))
    return render_template("customer/signup.html")


@app.route("/searchflights", methods=["GET", "POST"])
@login_required
def searfly():
    if request.method == "GET": return prediction_page()
    source, destination = request.form.get("source_city", "").strip(), request.form.get("destination_city", "").strip()
    departure_text, return_text = request.form.get("departure_date", ""), request.form.get("return_date", "")
    travel_class, stops_filter = request.form.get("travel_class", "Economy"), request.form.get("stops", "Any")
    try:
        travellers, departure = int(request.form.get("travellers", "0")), datetime.strptime(departure_text, "%Y-%m-%d").date()
        return_date = datetime.strptime(return_text, "%Y-%m-%d").date() if return_text else None
    except ValueError:
        flash("Enter a valid date and traveller count.", "danger"); return render_template("customer/search_flights.html")
    if source.lower() not in city_codes or destination.lower() not in city_codes or source.lower() == destination.lower():
        flash("Choose two different supported cities.", "danger"); return render_template("customer/search_flights.html")
    if departure < date.today() or (return_date and return_date < departure) or not 1 <= travellers <= 9 or stops_filter not in {"Any", "Non-stop", "1", "2"}:
        flash("Check your dates, stops filter, and traveller count (1–9).", "danger"); return render_template("customer/search_flights.html")
    relationship = db_cursor()
    relationship.execute("""INSERT INTO flight_searches (user_id,source_city,destination_city,departure_date,return_date,travellers,travel_class,stops_filter)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (session["user_id"], source, destination, departure, return_date, travellers, travel_class, stops_filter))
    search_id = relationship.lastrowid; mysql.connection.commit()
    api_key = os.getenv("IGNAV_API_KEY")
    if not api_key:
        relationship.close(); flash("Flight search is not configured. Add IGNAV_API_KEY to .env.", "danger"); return render_template("customer/search_flights.html")
    data = {"origin": city_codes[source.lower()], "destination": city_codes[destination.lower()], "departure_date": departure_text, "adults": travellers, "cabin_class": travel_class.lower().replace(" ", "_"), "market": "IN"}
    if stops_filter != "Any": data["max_stops"] = 0 if stops_filter == "Non-stop" else int(stops_filter)
    try:
        response = requests.post("https://ignav.com/api/fares/one-way", headers={"X-Api-Key": api_key, "Content-Type": "application/json"}, json=data, timeout=20)
        response.raise_for_status(); itineraries = response.json().get("itineraries", [])
    except (requests.RequestException, ValueError):
        relationship.close(); flash("The flight provider could not return results. Please try again.", "danger"); return render_template("customer/search_flights.html")
    flights = []
    for raw in itineraries:
        flight = parse_api_flight(raw)
        if not flight or flight["price"] is None or (stops_filter != "Any" and flight["stops"] > data["max_stops"]): continue
        relationship.execute("""INSERT INTO flight_results (search_id,airline,flight_number,route,departure_datetime,arrival_datetime,duration_minutes,stops,layover,travel_class,price,price_status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (search_id, flight["airline"], flight["flight_number"], flight["route"], flight["departure_datetime"], flight["arrival_datetime"], flight["duration_minutes"], flight["stops"], flight["layover"], flight["travel_class"], flight["price"], flight["price_status"]))
        flight["flight_id"] = relationship.lastrowid; flights.append(flight)
    mysql.connection.commit(); relationship.close()
    return render_template("customer/flight_results.html", flights=flights, origin=city_codes[source.lower()], destination_code=city_codes[destination.lower()])


@app.route("/predict-price", methods=["POST"])
@login_required
def predict_price():
    """Return an airline-only ML baseline; it never changes provider flight fares."""
    airline = request.form.get("airline", "").strip()
    if not airline:
        return prediction_page(prediction_error="Enter an airline name to estimate a fare."), 400
    if price_model is None:
        return prediction_page(prediction_error=price_model_error), 503
    try:
        estimate = float(price_model.predict(pd.DataFrame({"airline": [airline]}))[0])
    except (ValueError, TypeError, KeyError, AttributeError):
        app.logger.exception("Price-model prediction failed")
        return prediction_page(prediction_error="The estimate could not be calculated. Please try another airline."), 422
    if estimate < 0:
        return prediction_page(prediction_error="The model returned an invalid estimate."), 422
    return prediction_page(predicted_fare=estimate, prediction_airline=airline)


@app.route("/flight_details")
@login_required
def flight_details():
    flight_id = request.args.get("flight_id", type=int); selected_flight = user_flight(flight_id) if flight_id else None
    if not selected_flight: abort(404)
    return render_template("customer/flight_details.html", selected_flight=selected_flight)


@app.route("/seat_selection")
@login_required
def seat_select():
    flight_id = request.args.get("flight_id", type=int); selected_flight = user_flight(flight_id) if flight_id else None
    if not selected_flight: abort(404)
    return render_template("customer/seat_selection.html", selected_flight=selected_flight, travellers=int(selected_flight["travellers"]), occupied_seats=occupied_seats(flight_id), selected_seats=[])


@app.route("/passenger_details")
@login_required
def pass_det():
    flight_id = request.args.get("flight_id", type=int); selected_flight = user_flight(flight_id) if flight_id else None
    if not selected_flight: abort(404)
    seats, error = validate_seats(flight_id, request.args.getlist("seat_number"), int(selected_flight["travellers"]))
    if error: return render_template("customer/seat_selection.html", selected_flight=selected_flight, travellers=int(selected_flight["travellers"]), occupied_seats=occupied_seats(flight_id), selected_seats=request.args.getlist("seat_number"), error_message=error), 400
    return render_template("customer/passenger_details.html", selected_flight=selected_flight, selected_seats=seats, travellers=len(seats), form_data={})


@app.route("/confirm_pay", methods=["POST"])
@login_required
def con_pay():
    flight_id = request.form.get("flight_id", type=int); selected_flight = user_flight(flight_id) if flight_id else None
    if not selected_flight: abort(404)
    count = int(selected_flight["travellers"]); seats, seat_error = validate_seats(flight_id, request.form.getlist("seat_number"), count)
    keys = ("passenger_name[]", "age[]", "gender[]", "id_type[]", "id_number[]", "email[]", "mobile_number[]")
    fields = {key: request.form.getlist(key) for key in keys}; passengers = []
    if all(len(values) == count for values in fields.values()):
        for index in range(count):
            try: age = int(fields["age[]"][index])
            except ValueError: age = 0
            passengers.append({"name": fields["passenger_name[]"][index].strip(), "age": age, "gender": fields["gender[]"][index], "id_type": fields["id_type[]"][index], "id_number": fields["id_number[]"][index].strip(), "email": fields["email[]"][index].strip(), "mobile": fields["mobile_number[]"][index].strip()})
    invalid = len(passengers) != count or any(not p["name"] or not 1 <= p["age"] <= 120 or p["gender"] not in {"Female", "Male", "Other"} or p["id_type"] not in {"Aadhaar Card", "Passport", "Driving License"} or not p["id_number"] or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", p["email"]) or not p["mobile"] for p in passengers)
    if seat_error or invalid:
        flash(seat_error or "Please complete every passenger field with valid details.", "danger")
        return render_template("customer/passenger_details.html", selected_flight=selected_flight, selected_seats=request.form.getlist("seat_number"), travellers=count, form_data=fields), 400
    relationship = db_cursor()
    try:
        booking_pnr = secrets.token_hex(4).upper()
        relationship.execute("INSERT INTO bookings (user_id,flight_id,pnr,total_fare,booking_status) VALUES (%s,%s,%s,%s,%s)", (session["user_id"], flight_id, booking_pnr, selected_flight["price"], "Pending payment"))
        booking_id = relationship.lastrowid
        for index, passenger in enumerate(passengers):
            relationship.execute("""INSERT INTO passengers (booking_id,flight_id,user_id,seat_number,passenger_name,age,gender,id_type,id_number,email,mobile_number)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (booking_id, flight_id, session["user_id"], seats[index], passenger["name"], passenger["age"], passenger["gender"], passenger["id_type"], passenger["id_number"], passenger["email"], passenger["mobile"]))
        mysql.connection.commit()
    except Exception:
        # Keep the transaction safe, but do not silently send the user back to seats.
        # The application log has the technical cause; the browser gets a safe next step.
        mysql.connection.rollback()
        relationship.close()
        app.logger.exception("Booking creation failed")
        return render_template(
            "customer/passenger_details.html",
            selected_flight=selected_flight,
            selected_seats=seats,
            travellers=count,
            form_data=fields,
            error_message="Your booking could not be saved. Check that MySQL is running and apply migrations/001_booking_integrity.sql before trying again.",
        ), 500
    relationship.close(); return redirect(url_for("payment", booking_id=booking_id))


def owned_booking(booking_id):
    relationship = db_cursor()
    relationship.execute("""SELECT b.*,fr.airline,fr.flight_number,fr.route,fr.departure_datetime,fr.arrival_datetime,fr.travel_class
        FROM bookings b JOIN flight_results fr ON b.flight_id=fr.flight_id WHERE b.booking_id=%s AND b.user_id=%s""", (booking_id, session["user_id"]))
    booking = relationship.fetchone()
    if booking:
        relationship.execute("SELECT passenger_name,seat_number FROM passengers WHERE booking_id=%s ORDER BY passenger_id", (booking_id,)); booking["passengers"] = relationship.fetchall()
    relationship.close(); return booking


@app.route("/confirm_pay")
@login_required
def payment():
    booking_id = request.args.get("booking_id", type=int); booking = owned_booking(booking_id) if booking_id else None
    if not booking: abort(404)
    return render_template("customer/confirm_pay.html", booking=booking)


@app.route("/booking_confirmation", methods=["POST", "GET"])
@login_required
def book_con():
    booking_id = request.form.get("booking_id", type=int) if request.method == "POST" else request.args.get("booking_id", type=int)
    booking = owned_booking(booking_id) if booking_id else None
    if not booking: abort(404)
    if request.method == "POST" and booking["booking_status"] == "Pending payment":
        relationship = db_cursor(); relationship.execute("UPDATE bookings SET booking_status=%s WHERE booking_id=%s AND user_id=%s", ("Confirmed (demo payment)", booking_id, session["user_id"])); mysql.connection.commit(); relationship.close()
        return redirect(url_for("book_con", booking_id=booking_id))
    return render_template("customer/booking_confirmation.html", booking=booking)


@app.route("/mybookings")
@login_required
def my_book():
    relationship = db_cursor(); relationship.execute("""SELECT b.*,fr.airline,fr.flight_number,fr.route,fr.departure_datetime,(SELECT COUNT(*) FROM passengers p WHERE p.booking_id=b.booking_id) passenger_count
        FROM bookings b JOIN flight_results fr ON b.flight_id=fr.flight_id WHERE b.user_id=%s ORDER BY b.booking_date DESC""", (session["user_id"],)); bookings = relationship.fetchall(); relationship.close()
    return render_template("customer/my_bookings.html", bookings=bookings)


@app.route("/boardingpass")
@login_required
def board_pass():
    booking_id = request.args.get("booking_id", type=int); booking = owned_booking(booking_id) if booking_id else None
    if not booking: abort(404)
    return render_template("customer/boarding_pass.html", booking=booking)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def prof():
    relationship = db_cursor()
    if request.method == "POST":
        name, email, mobile = request.form.get("name", "").strip(), request.form.get("email", "").strip().lower(), request.form.get("mobile", "").strip()
        relationship.execute("SELECT user_id FROM users WHERE (email=%s OR mobile=%s) AND user_id<>%s", (email, mobile, session["user_id"]))
        if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or not mobile or relationship.fetchone(): flash("Enter unique, valid profile details.", "danger")
        else: relationship.execute("UPDATE users SET full_name=%s,email=%s,mobile=%s WHERE user_id=%s", (name, email, mobile, session["user_id"])); mysql.connection.commit(); session["name"] = name.split()[0]; flash("Profile updated.", "success")
    relationship.execute("SELECT user_id,full_name,email,mobile FROM users WHERE user_id=%s", (session["user_id"],)); user = relationship.fetchone(); relationship.close()
    return render_template("customer/profile.html", user=user)


def admin_form_error(message, template, **values):
    flash(message, "danger")
    return render_template(template, **values), 400


def schedule_values(form):
    try:
        departure = datetime.fromisoformat(form.get("departure_datetime", ""))
        arrival = datetime.fromisoformat(form.get("arrival_datetime", ""))
        duration = int(form.get("duration_minutes", "0"))
        capacity = int(form.get("seat_capacity", "0"))
        fare = float(form.get("fare", "-1"))
        aircraft_id = int(form["aircraft_id"]) if form.get("aircraft_id") else None
    except ValueError:
        return None, "Enter valid dates, duration, capacity, and fare."
    data = {"airline": form.get("airline", "").strip(), "flight_number": form.get("flight_number", "").strip().upper(), "origin_code": form.get("origin_code", "").strip().upper(), "destination_code": form.get("destination_code", "").strip().upper(), "departure": departure, "arrival": arrival, "duration": duration, "capacity": capacity, "travel_class": form.get("travel_class", "Economy"), "fare": fare, "status": form.get("status", "Active"), "aircraft_id": aircraft_id}
    if not data["airline"] or not re.fullmatch(r"[A-Z]{3}", data["origin_code"]) or not re.fullmatch(r"[A-Z]{3}", data["destination_code"]): return None, "Enter an airline and valid three-letter airport codes."
    if data["origin_code"] == data["destination_code"] or arrival <= departure or duration <= 0 or capacity <= 0 or fare < 0 or data["status"] not in {"Active", "Inactive", "Cancelled"}: return None, "Check the route, timing, capacity, fare, and status."
    return data, None


def schedule_form_display(form):
    """Convert valid HTML datetime values for the edit template after an error."""
    values = dict(form)
    for key in ("departure_datetime", "arrival_datetime"):
        try:
            values[key] = datetime.fromisoformat(values.get(key, ""))
        except (TypeError, ValueError):
            values[key] = None
    return values


def aircraft_values(form):
    try:
        total, economy, business, first = (int(form.get(key, "0")) for key in ("total_capacity", "economy_capacity", "business_capacity", "first_capacity"))
    except ValueError:
        return None, "Seat capacities must be whole numbers."
    data = {"registration": form.get("registration_number", "").strip().upper(), "manufacturer": form.get("manufacturer", "").strip(), "model": form.get("model", "").strip(), "total": total, "economy": economy, "business": business, "first": first, "status": form.get("status", "Active")}
    if not data["registration"] or not data["manufacturer"] or not data["model"] or total <= 0 or min(economy, business, first) < 0 or economy + business + first > total or data["status"] not in {"Active", "Inactive", "Maintenance"}: return None, "Complete all fields and ensure cabin seats do not exceed total capacity."
    return data, None


@app.route("/dashboard")
@admin_required
def dash():
    relationship = db_cursor()
    relationship.execute("SELECT COUNT(*) total FROM users"); total_users = relationship.fetchone()["total"]
    relationship.execute("SELECT COUNT(*) total FROM bookings"); total_bookings = relationship.fetchone()["total"]
    relationship.execute("SELECT COUNT(*) total FROM passengers"); total_passengers = relationship.fetchone()["total"]
    relationship.execute("SELECT COALESCE(SUM(total_fare),0) total FROM bookings WHERE booking_status IN ('Confirmed', 'Confirmed (demo payment)')"); revenue = relationship.fetchone()["total"]
    relationship.execute("SELECT booking_status, COUNT(*) total FROM bookings GROUP BY booking_status"); status_counts = relationship.fetchall()
    relationship.execute("""SELECT b.booking_id,b.pnr,b.booking_status,b.booking_date,fr.airline,fr.flight_number,
        (SELECT passenger_name FROM passengers p WHERE p.booking_id=b.booking_id ORDER BY passenger_id LIMIT 1) passenger_name
        FROM bookings b JOIN flight_results fr ON fr.flight_id=b.flight_id ORDER BY b.booking_date DESC LIMIT 8"""); recent_bookings = relationship.fetchall()
    relationship.close()
    ready = admin_schema_ready()
    scheduled_flights = active_aircraft = 0
    if ready:
        relationship = db_cursor(); relationship.execute("SELECT COUNT(*) total FROM scheduled_flights"); scheduled_flights = relationship.fetchone()["total"]
        relationship.execute("SELECT COUNT(*) total FROM aircraft WHERE status='Active'"); active_aircraft = relationship.fetchone()["total"]; relationship.close()
    return render_template("admin/dashboard.html", total_users=total_users, total_bookings=total_bookings, total_passengers=total_passengers, revenue=revenue, scheduled_flights=scheduled_flights, active_aircraft=active_aircraft, status_counts=status_counts, recent_bookings=recent_bookings, schema_ready=ready)


@app.route("/flights")
@admin_required
def flight():
    if not admin_schema_ready(): return render_template("admin/flights.html", flights=[], schema_ready=False)
    query, values = """SELECT sf.*,a.registration_number FROM scheduled_flights sf LEFT JOIN aircraft a ON a.aircraft_id=sf.aircraft_id WHERE 1=1""", []
    if request.args.get("q"):
        query += " AND (sf.flight_number LIKE %s OR sf.airline LIKE %s OR sf.origin_code LIKE %s OR sf.destination_code LIKE %s)"; values += [f"%{request.args['q'].strip()}%"] * 4
    for column, arg in (("status", "status"), ("origin_code", "origin"), ("destination_code", "destination")):
        if request.args.get(arg): query += f" AND sf.{column}=%s"; values.append(request.args[arg].strip().upper() if arg != "status" else request.args[arg])
    if request.args.get("date"): query += " AND DATE(sf.departure_datetime)=%s"; values.append(request.args["date"])
    relationship = db_cursor(); relationship.execute(query + " ORDER BY sf.departure_datetime DESC LIMIT 100", tuple(values)); flights = relationship.fetchall(); relationship.close()
    return render_template("admin/flights.html", flights=flights, schema_ready=True)


@app.route("/flights/add", methods=["GET", "POST"])
@admin_required
def flight_add():
    if not admin_schema_ready(): abort(503)
    relationship = db_cursor(); relationship.execute("SELECT aircraft_id,registration_number,model,total_capacity FROM aircraft WHERE status='Active' ORDER BY registration_number"); aircraft = relationship.fetchall(); relationship.close()
    if request.method == "POST":
        if not valid_csrf_token(): abort(400)
        data, error = schedule_values(request.form)
        if error: return admin_form_error(error, "admin/flights_add.html", aircraft=aircraft, scheduled_flight=schedule_form_display(request.form))
        relationship = db_cursor()
        try:
            relationship.execute("""INSERT INTO scheduled_flights (airline,flight_number,origin_code,destination_code,departure_datetime,arrival_datetime,duration_minutes,seat_capacity,travel_class,fare,status,aircraft_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (data["airline"],data["flight_number"],data["origin_code"],data["destination_code"],data["departure"],data["arrival"],data["duration"],data["capacity"],data["travel_class"],data["fare"],data["status"],data["aircraft_id"]))
            mysql.connection.commit(); flash("Scheduled flight created.", "success")
        except Exception:
            mysql.connection.rollback(); flash("Could not save that flight. Its number and departure time must be unique.", "danger"); return render_template("admin/flights_add.html", aircraft=aircraft, scheduled_flight=schedule_form_display(request.form)), 400
        finally: relationship.close()
        return redirect(url_for("flight"))
    return render_template("admin/flights_add.html", aircraft=aircraft, scheduled_flight={})


@app.route("/flights/<int:schedule_id>/edit", methods=["GET", "POST"])
@admin_required
def flight_edit(schedule_id):
    if not admin_schema_ready(): abort(503)
    relationship = db_cursor(); relationship.execute("SELECT * FROM scheduled_flights WHERE schedule_id=%s", (schedule_id,)); scheduled_flight = relationship.fetchone(); relationship.execute("SELECT aircraft_id,registration_number,model,total_capacity FROM aircraft WHERE status='Active' OR aircraft_id=%s ORDER BY registration_number", (scheduled_flight["aircraft_id"] if scheduled_flight else 0,)); aircraft = relationship.fetchall(); relationship.close()
    if not scheduled_flight: abort(404)
    if request.method == "POST":
        if not valid_csrf_token(): abort(400)
        data, error = schedule_values(request.form)
        if error: return admin_form_error(error, "admin/flights_add.html", aircraft=aircraft, scheduled_flight=schedule_form_display(request.form))
        relationship = db_cursor()
        try:
            relationship.execute("""UPDATE scheduled_flights SET airline=%s,flight_number=%s,origin_code=%s,destination_code=%s,departure_datetime=%s,arrival_datetime=%s,duration_minutes=%s,seat_capacity=%s,travel_class=%s,fare=%s,status=%s,aircraft_id=%s WHERE schedule_id=%s""", (data["airline"],data["flight_number"],data["origin_code"],data["destination_code"],data["departure"],data["arrival"],data["duration"],data["capacity"],data["travel_class"],data["fare"],data["status"],data["aircraft_id"],schedule_id)); mysql.connection.commit(); flash("Scheduled flight updated.", "success")
        except Exception:
            mysql.connection.rollback(); flash("Could not update that flight.", "danger"); return render_template("admin/flights_add.html", aircraft=aircraft, scheduled_flight=schedule_form_display(request.form)), 400
        finally: relationship.close()
        return redirect(url_for("flight"))
    return render_template("admin/flights_add.html", aircraft=aircraft, scheduled_flight=scheduled_flight)


@app.route("/flights/<int:schedule_id>/status", methods=["POST"])
@admin_required
def flight_status(schedule_id):
    if not admin_schema_ready() or not valid_csrf_token(): abort(400)
    status = request.form.get("status")
    if status not in {"Active", "Inactive", "Cancelled"}: abort(400)
    relationship = db_cursor(); relationship.execute("UPDATE scheduled_flights SET status=%s WHERE schedule_id=%s", (status, schedule_id))
    if not relationship.rowcount: relationship.close(); abort(404)
    mysql.connection.commit(); relationship.close()
    flash("Flight status updated.", "success"); return redirect(url_for("flight"))


@app.route("/aircraft")
@admin_required
def aircraft():
    if not admin_schema_ready(): return render_template("admin/aircraft.html", aircraft=[], schema_ready=False)
    query, values = "SELECT * FROM aircraft WHERE 1=1", []
    if request.args.get("q"): query += " AND (registration_number LIKE %s OR model LIKE %s OR manufacturer LIKE %s)"; values += [f"%{request.args['q'].strip()}%"] * 3
    if request.args.get("status"): query += " AND status=%s"; values.append(request.args["status"])
    relationship = db_cursor(); relationship.execute(query + " ORDER BY registration_number LIMIT 100", tuple(values)); aircraft_records = relationship.fetchall(); relationship.close()
    return render_template("admin/aircraft.html", aircraft=aircraft_records, schema_ready=True)


@app.route("/aircraft/add", methods=["GET", "POST"])
@admin_required
def aircraft_add():
    if not admin_schema_ready(): abort(503)
    if request.method == "POST":
        if not valid_csrf_token(): abort(400)
        data, error = aircraft_values(request.form)
        if error: return admin_form_error(error, "admin/aircraft_edit.html", aircraft_record=request.form)
        relationship = db_cursor()
        try:
            relationship.execute("""INSERT INTO aircraft (registration_number,manufacturer,model,total_capacity,economy_capacity,business_capacity,first_capacity,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (data["registration"],data["manufacturer"],data["model"],data["total"],data["economy"],data["business"],data["first"],data["status"])); mysql.connection.commit(); flash("Aircraft created.", "success")
        except Exception:
            mysql.connection.rollback(); flash("Could not save the aircraft. Registration numbers must be unique.", "danger"); return render_template("admin/aircraft_edit.html", aircraft_record=request.form), 400
        finally: relationship.close()
        return redirect(url_for("aircraft"))
    return render_template("admin/aircraft_edit.html", aircraft_record={})


@app.route("/aircraft/<int:aircraft_id>/edit", methods=["GET", "POST"])
@admin_required
def aircraft_edit(aircraft_id):
    if not admin_schema_ready(): abort(503)
    relationship = db_cursor(); relationship.execute("SELECT * FROM aircraft WHERE aircraft_id=%s", (aircraft_id,)); aircraft_record = relationship.fetchone(); relationship.close()
    if not aircraft_record: abort(404)
    if request.method == "POST":
        if not valid_csrf_token(): abort(400)
        data, error = aircraft_values(request.form)
        if error: return admin_form_error(error, "admin/aircraft_edit.html", aircraft_record=request.form)
        relationship = db_cursor()
        try:
            relationship.execute("""UPDATE aircraft SET registration_number=%s,manufacturer=%s,model=%s,total_capacity=%s,economy_capacity=%s,business_capacity=%s,first_capacity=%s,status=%s WHERE aircraft_id=%s""", (data["registration"],data["manufacturer"],data["model"],data["total"],data["economy"],data["business"],data["first"],data["status"],aircraft_id)); mysql.connection.commit(); flash("Aircraft updated.", "success")
        except Exception:
            mysql.connection.rollback(); flash("Could not update the aircraft.", "danger"); return render_template("admin/aircraft_edit.html", aircraft_record=request.form), 400
        finally: relationship.close()
        return redirect(url_for("aircraft"))
    return render_template("admin/aircraft_edit.html", aircraft_record=aircraft_record)


@app.route("/aircraft/<int:aircraft_id>/status", methods=["POST"])
@admin_required
def aircraft_status(aircraft_id):
    if not admin_schema_ready() or not valid_csrf_token(): abort(400)
    status = request.form.get("status")
    if status not in {"Active", "Inactive", "Maintenance"}: abort(400)
    relationship = db_cursor(); relationship.execute("UPDATE aircraft SET status=%s WHERE aircraft_id=%s", (status, aircraft_id))
    if not relationship.rowcount: relationship.close(); abort(404)
    mysql.connection.commit(); relationship.close()
    flash("Aircraft status updated.", "success"); return redirect(url_for("aircraft"))


@app.route("/bookings")
@admin_required
def book():
    query, values = """SELECT DISTINCT b.booking_id,b.pnr,b.booking_status,b.total_fare,b.booking_date,fr.airline,fr.flight_number,fr.route,
        (SELECT passenger_name FROM passengers p2 WHERE p2.booking_id=b.booking_id ORDER BY passenger_id LIMIT 1) passenger_name
        FROM bookings b JOIN flight_results fr ON b.flight_id=fr.flight_id LEFT JOIN passengers p ON p.booking_id=b.booking_id WHERE 1=1""", []
    if request.args.get("q"):
        query += " AND (b.pnr LIKE %s OR CAST(b.booking_id AS CHAR) LIKE %s OR fr.flight_number LIKE %s OR p.passenger_name LIKE %s)"; values += [f"%{request.args['q'].strip()}%"] * 4
    if request.args.get("status"): query += " AND b.booking_status=%s"; values.append(request.args["status"])
    if request.args.get("date"): query += " AND DATE(b.booking_date)=%s"; values.append(request.args["date"])
    relationship = db_cursor(); relationship.execute(query + " ORDER BY b.booking_date DESC LIMIT 100", tuple(values)); bookings = relationship.fetchall(); relationship.close()
    return render_template("admin/bookings.html", bookings=bookings)


@app.route("/bookings/<int:booking_id>")
@admin_required
def booking_detail(booking_id):
    relationship = db_cursor(); relationship.execute("""SELECT b.*,u.full_name,fr.airline,fr.flight_number,fr.route,fr.departure_datetime,fr.arrival_datetime,fr.travel_class
        FROM bookings b JOIN users u ON u.user_id=b.user_id JOIN flight_results fr ON fr.flight_id=b.flight_id WHERE b.booking_id=%s""", (booking_id,)); booking = relationship.fetchone()
    if booking: relationship.execute("SELECT passenger_name,seat_number,age,gender,email,mobile_number FROM passengers WHERE booking_id=%s ORDER BY passenger_id", (booking_id,)); passengers = relationship.fetchall()
    else: passengers = []
    relationship.close()
    if not booking: abort(404)
    return render_template("admin/booking_detail.html", booking=booking, passengers=passengers)


@app.route("/bookings/<int:booking_id>/status", methods=["POST"])
@admin_required
def booking_status(booking_id):
    if not valid_csrf_token(): abort(400)
    if request.form.get("status") != "Cancelled": abort(400)
    relationship = db_cursor(); relationship.execute("SELECT booking_status FROM bookings WHERE booking_id=%s", (booking_id,)); booking = relationship.fetchone()
    if not booking: relationship.close(); abort(404)
    if booking["booking_status"] == "Cancelled": relationship.close(); flash("Booking is already cancelled.", "warning"); return redirect(url_for("booking_detail", booking_id=booking_id))
    relationship.execute("UPDATE bookings SET booking_status='Cancelled' WHERE booking_id=%s", (booking_id,)); mysql.connection.commit(); relationship.close()
    flash("Booking was marked cancelled. No refund is processed by the demo payment flow.", "success"); return redirect(url_for("booking_detail", booking_id=booking_id))


@app.route("/api/flights/<int:flight_id>")
@login_required
def flight_api(flight_id):
    flight = user_flight(flight_id)
    if not flight: return jsonify({"error": "Flight not found"}), 404
    keys = ("flight_id", "airline", "flight_number", "route", "departure_datetime", "arrival_datetime", "duration_minutes", "stops", "travel_class", "price", "price_status")
    return jsonify({key: str(flight[key]) if isinstance(flight[key], (datetime, date)) else flight[key] for key in keys})


if __name__ == "__main__":
    if not app.secret_key: raise RuntimeError("SECRET_KEY is required. Copy .env.example to .env and set it.")
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
