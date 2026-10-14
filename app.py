from flask import Flask, render_template, request, session
from flask_mysqldb import MySQL
import requests
import os
from dotenv import load_dotenv

from datetime import datetime


load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY")

app.config["MYSQL_HOST"] = os.getenv("MYSQL_HOST")
app.config["MYSQL_USER"] = os.getenv("MYSQL_USER")
app.config["MYSQL_PASSWORD"] = os.getenv("MYSQL_PASSWORD")
app.config["MYSQL_DB"] = os.getenv("MYSQL_DB")

mysql = MySQL(app)




city_codes = {
    "mumbai": "BOM",
    "delhi": "DEL",
    "bangalore": "BLR",
    "bengaluru": "BLR",
    "hyderabad": "HYD",
    "chennai": "MAA",
    "kolkata": "CCU",
    "ahmedabad": "AMD",
    "lucknow": "LKO",
    "pune": "PNQ",
    "jaipur": "JAI",
    "goa": "GOI"
}

airport_names = {
    "BOM": "Mumbai",
    "DEL": "Delhi",
    "BLR": "Bangalore",
    "HYD": "Hyderabad",
    "MAA": "Chennai",
    "CCU": "Kolkata",
    "AMD": "Ahmedabad",
    "LKO": "Lucknow",
    "PNQ": "Pune",
    "JAI": "Jaipur",
    "GOI": "Goa"
}

def format_time(time):
    time = datetime.fromisoformat(time)
    return time.strftime("%I:%M %p")


def format_duration(minutes):
    hours = minutes // 60
    remaining_minutes = minutes % 60

    return f"{hours}h {remaining_minutes:02d}m"


def get_airport_name(code):
    return airport_names.get(code, code)


## ~~~~ Creating function for session login check ~~~~~
def check_login():
    if "name" not in session:
        return False
    return True


###  ~~~~~~~~~ CUSTOMER ~~~~~~~~


@app.route("/")
def home():
    return render_template("customer/home.html")


@app.route("/login", methods=["GET", "POST"])
def log():

    if request.method == "POST":
        m = request.form["txtemail"]
        p = request.form["txtpassword"]

        ##1st step: OPEN CONNECTION
        relationship = mysql.connection.cursor()

        ##2nd step: query execute
        relationship.execute("select * from users where email=%s", (m,))

        ##3rd step:fetchone stands for taking one specific row
        user = relationship.fetchone()

        ##4th: connection close
        relationship.close()

        ##4th: Logic
        if user == None:
            msg = "email doesn't exists"  ## sending msg back to the login page
            return render_template("customer/login.html", message=msg)

        if user[4] == p and user[2] == m:
            session["user_id"] = user[0]
            session["name"] = user[1].split()[
                0
            ]  ## session so website can know which user is logged in
            return render_template("customer/home.html")
        else:
            msg = "Invalid credentials"  ## sending msg back to the login page bro
            return render_template("customer/login.html", message=msg)
    return render_template("customer/login.html")


@app.route("/logout")
def logout():
    session.clear()
    return render_template("customer/home.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():

    if request.method == "POST":

        name = request.form["txtname"]
        email = request.form["txtemail"]
        mobile = request.form["txtmob"]
        password = request.form["txtpassword"]
        confirm_password = request.form["txtconfirm"]

        if password != confirm_password:
            msg = "Password do not match"
            return render_template("customer/signup.html", message=msg)
        
        
        # Open cursor
        relationship = mysql.connection.cursor()

        # First, check whether the email or mobile already exists
        relationship.execute(
            "SELECT user_id FROM users WHERE email=%s OR mobile=%s",
            (email, mobile))

        existing_user = relationship.fetchone()

        # If user already exists, show message
        if existing_user:
            relationship.close()

            msg = "User already exists. Please log in."

            return render_template(
                "customer/signup.html",
                message=msg
            )


        ##between DB and backend connection is open
        relationship = mysql.connection.cursor()

        ## query execution
        relationship.execute(
            "insert into users(full_name,email,mobile,password) values (%s,%s,%s,%s)",
            (name, email, mobile, password),
        )

        ##to save data in database
        mysql.connection.commit()

        ## to close connection btn Db and backend
        relationship.close()

        return render_template("customer/login.html")

    return render_template("customer/signup.html")


@app.route("/searchflights", methods=["GET", "POST"])
def searfly():

    if check_login() == False:
        return render_template("customer/login.html")

    if request.method == "POST":

        source = request.form["source_city"]
        destination = request.form["destination_city"]
        departure = request.form["departure_date"]
        return_date = request.form["return_date"]
        travellers = request.form["travellers"]
        travel_class = request.form["travel_class"]
        stop = request.form["stops"]
        
        origin = city_codes[source.lower()]
        destination_code = city_codes[destination.lower()]

        print(source)
        print(destination)
        print(departure)
        print(return_date)
        print(travellers)
        print(travel_class)
        print(stop)

        if return_date == "":
            return_date= None

        travellers = int(travellers)

        ## building connection btn db and backend
        relationship = mysql.connection.cursor()

        ##query execute
        relationship.execute(
            "insert into flight_searches (user_id, source_city, destination_city, departure_date,return_date, travellers, travel_class, stops_filter) values (%s,%s,%s,%s,%s,%s,%s,%s)",(
                session["user_id"],
                source,
                destination,
                departure,
                return_date,
                travellers,
                travel_class,
                stop,
            )
        )
        search_id = relationship.lastrowid

        mysql.connection.commit()
        print("saved successfully")

        ## to close connection btn Db and backend
        relationship.close()
        
        
        print("Search ID:", search_id)
        
        origin = city_codes[source.lower()]
        destination_code = city_codes[destination.lower()]
        
        travel_class = travel_class.lower()

        api_data = {"origin": origin,"destination": destination_code,"departure_date": departure,"adults": travellers,"cabin_class": travel_class,"market": "IN"}

        if stop == "Any":
            max_stops = None
        elif stop == "Non-stop":
            max_stops = 0
        elif stop == "1 stop":
            max_stops = 1
        elif stop == "2 stops":
            max_stops = 2

        api_key = os.getenv("IGNAV_API_KEY")

        headers = {
            "X-Api-Key": api_key,
            "Content-Type": "application/json"
        }

        response = requests.post(
            "https://ignav.com/api/fares/one-way",
            headers=headers,
            json=api_data
        )

        print("API Status:", response.status_code)
        print("API Response:", response.json())  ##
        
        
        print("API Status:", response.status_code)

        api_response = response.json()

        print("API Response:", api_response)

        flights = api_response["itineraries"]
        
        relationship=mysql.connection.cursor()
        
        for flight in flights:
            

            airline = flight["outbound"]["carrier"]
            price = flight["price"]["amount"]

            segments = flight["outbound"]["segments"]

            first_segment = segments[0]
            last_segment = segments[-1]

            flight_number = first_segment["flight_number"]
            departure = first_segment["departure_airport"]
            arrival = last_segment["arrival_airport"]
            
            route = " → ".join(segment["departure_airport"] for segment in segments)

            route = route + " → " + last_segment["arrival_airport"]

            departure_datetime = first_segment["departure_time_local"].replace("T", " ")

            arrival_datetime = last_segment["arrival_time_local"].replace("T", " ")

            duration_minutes = flight["outbound"]["duration_minutes"]

            stops = len(segments) - 1

            travel_class = flight["cabin_class"]

            price_status = flight["price"]["status"]
            
            
            relationship.execute("""
            INSERT INTO flight_results
            (search_id,
            airline,
            flight_number,
            route,
            departure_datetime,
            arrival_datetime,
            duration_minutes,
            stops,
            layover,
            travel_class,
            price,
            price_status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
            search_id,
            airline,
            flight_number,
            route,
            departure_datetime,
            arrival_datetime,
            duration_minutes,
            stops,
            None,
            travel_class,
            price,
            price_status))
            
            flight["flight_id"] = relationship.lastrowid
            print("SAVED FLIGHT ID:", flight["flight_id"])
            
            
            

            print("SEARCH ID:", search_id)
            print("AIRLINE:", airline)
            print("PRICE:", price)
            print("FLIGHT NUMBER:", flight_number)
            print("FROM:", departure)
            print("TO:", arrival)
            print("--------------------")
            
        mysql.connection.commit()
        relationship.close()
        
        return render_template(
    "customer/flight_results.html",
    flights=flights,
    origin=origin,
    destination_code=destination_code,
    format_time=format_time,
    format_duration=format_duration,
    get_airport_name=get_airport_name
)

    return render_template("customer/search_flights.html")


@app.route("/mybookings")
def my_book():
    if check_login() == False:
        return render_template("customer/login.html")

    return render_template("customer/my_bookings.html")


@app.route("/boardingpass")
def board_pass():
    
    if check_login() == False:
            return render_template("customer/login.html")
    return render_template("customer/boarding_pass.html")



@app.route("/flight_details")
def flight_details():

    if check_login() == False:
        return render_template("customer/login.html")

    # Step 1: Get flight ID from the URL
    flight_id = request.args.get("flight_id", type=int)

    if flight_id is None:
        return "Please select a flight first.", 400

    # Step 2: Open cursor
    relationship = mysql.connection.cursor()

    # Step 3: Find the selected flight in MySQL
    relationship.execute("""
        SELECT fr.*
        FROM flight_results AS fr
        INNER JOIN flight_searches AS fs
            ON fr.search_id = fs.search_id
        WHERE fr.flight_id = %s
        AND fs.user_id = %s
    """, (flight_id, session["user_id"]))

    # Step 4: Get the matching database row
    selected_flight = relationship.fetchone()

    # Step 5: Close cursor
    relationship.close()

    # Step 6: Print result for testing
    print("REQUESTED FLIGHT ID:", flight_id)
    print("SELECTED FLIGHT:", selected_flight)

    if selected_flight is None:
        return "Flight not found for your account.", 404

    # Step 7: Display the page
    return render_template("customer/flight_details.html",selected_flight=selected_flight,
    format_time=format_time,
    format_duration=format_duration,
    get_airport_name=get_airport_name,)




@app.route("/seat_selection")
def seat_select():

    if check_login() == False:
        return render_template("customer/login.html")

    flight_id = request.args.get("flight_id", type=int)

    if flight_id is None:
        return "Please select a flight first.", 400

    relationship = mysql.connection.cursor()

    relationship.execute("""
        SELECT fr.*
        FROM flight_results AS fr
        INNER JOIN flight_searches AS fs
            ON fr.search_id = fs.search_id
        WHERE fr.flight_id = %s
        AND fs.user_id = %s
    """, (flight_id, session["user_id"]))

    selected_flight = relationship.fetchone()
    relationship.close()

    if selected_flight is None:
        return "Flight not found for your account.", 404

    return render_template(
        "customer/seat_selection.html",
        selected_flight=selected_flight
    )



@app.route("/passenger_details")
def pass_det():
    
    if check_login() == False:
            return render_template("customer/login.html")
        
    return render_template("customer/passenger_details.html")


@app.route("/confirm_pay")
def con_pay():
    
    if check_login() == False:
            return render_template("customer/login.html")
        
    return render_template("customer/confirm_pay.html")


@app.route("/booking_confirmation")
def book_con():
    
    if check_login() == False:
            return render_template("customer/login.html")
        
    return render_template("customer/booking_confirmation.html")


@app.route("/profile")
def prof():
    
    if check_login() == False:
            return render_template("customer/login.html")
        
    return render_template("customer/profile.html")


@app.route("/flight_results")
def flight_res():
    
    if check_login() == False:
            return render_template("customer/login.html")
        
    return render_template("customer/flight_results.html")


### ~~~~~~~ ADMIN ~~~~~~~~~~


@app.route("/dashboard")
def dash():
    return render_template("admin/dashboard.html")


@app.route("/flights")
def flight():
    return render_template("admin/flights.html")


@app.route("/flights_add")
def flight_add():
    return render_template("admin/flights_add.html")


@app.route("/aircraft")
def aircraft():
    return render_template("admin/aircraft.html")


@app.route("/aircraft_edit")
def airc_edit():
    return render_template("admin/aircraft.html")


@app.route("/bookings")
def book():
    return render_template("admin/bookings.html")


app.run(debug=True)
