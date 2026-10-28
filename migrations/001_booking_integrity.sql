-- Run this manually after backing up the safe_travels database.
-- It does not drop tables or existing records.
ALTER TABLE passengers ADD COLUMN booking_id INT NULL AFTER passenger_id;
ALTER TABLE passengers ADD INDEX idx_passengers_booking_id (booking_id);
ALTER TABLE passengers ADD CONSTRAINT fk_passengers_booking
  FOREIGN KEY (booking_id) REFERENCES bookings(booking_id);
ALTER TABLE passengers ADD CONSTRAINT unique_flight_seat UNIQUE (flight_id, seat_number);
ALTER TABLE bookings ADD CONSTRAINT unique_booking_pnr UNIQUE (pnr);
ALTER TABLE users ADD COLUMN is_admin TINYINT(1) NOT NULL DEFAULT 0;
-- Promote a known account only after it has been created:
-- UPDATE users SET is_admin=1 WHERE email='approved-admin@example.com';
