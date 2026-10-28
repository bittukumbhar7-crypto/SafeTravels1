-- Run once in MySQL Workbench after backing up safe_travels.
-- These independent tables are intentionally separate from customer flight_results.
CREATE TABLE aircraft (
  aircraft_id INT AUTO_INCREMENT PRIMARY KEY,
  registration_number VARCHAR(30) NOT NULL UNIQUE,
  manufacturer VARCHAR(80) NOT NULL,
  model VARCHAR(100) NOT NULL,
  total_capacity INT NOT NULL,
  economy_capacity INT NOT NULL DEFAULT 0,
  business_capacity INT NOT NULL DEFAULT 0,
  first_capacity INT NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'Active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT chk_aircraft_capacities CHECK (total_capacity > 0 AND economy_capacity >= 0 AND business_capacity >= 0 AND first_capacity >= 0 AND economy_capacity + business_capacity + first_capacity <= total_capacity)
);

CREATE TABLE scheduled_flights (
  schedule_id INT AUTO_INCREMENT PRIMARY KEY,
  airline VARCHAR(100) NOT NULL,
  flight_number VARCHAR(20) NOT NULL,
  origin_code VARCHAR(10) NOT NULL,
  destination_code VARCHAR(10) NOT NULL,
  departure_datetime DATETIME NOT NULL,
  arrival_datetime DATETIME NOT NULL,
  duration_minutes INT NOT NULL,
  seat_capacity INT NOT NULL,
  travel_class VARCHAR(30) NOT NULL DEFAULT 'Economy',
  fare DECIMAL(10,2) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'Active',
  aircraft_id INT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY unique_scheduled_flight (flight_number, departure_datetime),
  KEY idx_schedule_departure (departure_datetime),
  KEY idx_schedule_route (origin_code, destination_code),
  KEY idx_schedule_status (status),
  CONSTRAINT fk_scheduled_flight_aircraft FOREIGN KEY (aircraft_id) REFERENCES aircraft(aircraft_id),
  CONSTRAINT chk_scheduled_flight_values CHECK (origin_code <> destination_code AND arrival_datetime > departure_datetime AND duration_minutes > 0 AND seat_capacity > 0 AND fare >= 0)
);
