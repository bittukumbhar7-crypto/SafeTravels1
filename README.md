# SafeTravels

Flask/MySQL student flight-booking application using the iGNav one-way fare API. The customer flow is search → result → flight details → demonstration seat map → passenger details → pending booking → explicit demo payment → confirmation/travel document.

## Setup

1. `cd SafeTravels` and create/activate a virtual environment.
2. Run `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and set local secrets. Never commit `.env`.
4. Back up MySQL, inspect existing tables, then run `mysql -u USER -p safe_travels < migrations/001_booking_integrity.sql`.
5. Start with `python app.py`.

The migration adds `passengers.booking_id`, an explicit `users.is_admin` role, ensures a PNR is unique, and prevents the same `flight_id`/`seat_number` combination. It may fail if historical duplicates exist; identify and resolve those records before retrying. No migration is run automatically.

## API and payment

`IGNAV_API_KEY` is used only for `https://ignav.com/api/fares/one-way`. Return dates are stored but no round-trip endpoint has been verified, so results remain one-way. `GET /api/flights/<flight_id>` returns a selected flight only for its search owner after login.

Payment is demonstration-only: no card, CVV, UPI, or financial credential is collected or stored. A booking starts as `Pending payment` and changes to `Confirmed (demo payment)` only when the explicit demo action is submitted.

## ML experiment

Run `python train_price_model.py`. It writes `models/price_model.joblib` and `models/metrics.json` reproducibly (fixed split random state). The supplied CSV has 966 usable price rows but is missing reliable route/time/class features, so the `/predict-price` form is an airline-only baseline estimate. It must not replace a live iGNav API fare or be treated as a guaranteed future price. The rendered page discloses the saved held-out MAE, RMSE and R² metrics.

## Manual checks

Use two accounts to verify the ownership checks on `flight_details`, `boardingpass`, `booking_confirmation`, and `/api/flights/<id>`. Check 1, 2, and 3 selected seats, duplicate seat submissions, occupied seats, and a payment refresh. The unique MySQL constraint is the final protection against simultaneous seat booking.
