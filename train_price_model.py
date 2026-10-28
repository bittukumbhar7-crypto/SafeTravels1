"""Train the optional, limited fare-estimate model from the supplied CSV."""
from pathlib import Path
import json
import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

DATASET = Path(__file__).parent.parent / "flight-search-data.csv"
OUTPUT = Path(__file__).parent / "models"

data = pd.read_csv(DATASET)
data["price"] = pd.to_numeric(data["price"], errors="coerce")
data = data.dropna(subset=["price"])
# The supplied file has mostly airline and price; duration/stops are frequently missing.
# Airline is therefore the only defensible feature for this baseline estimate.
features = data[["airline"]].fillna("Unknown")
target = data["price"]
X_train, X_test, y_train, y_test = train_test_split(features, target, test_size=0.2, random_state=42)
pipeline = Pipeline([("prepare", ColumnTransformer([("airline", Pipeline([("fill", SimpleImputer(strategy="most_frequent")), ("encode", OneHotEncoder(handle_unknown="ignore"))]), ["airline"])])), ("model", RandomForestRegressor(n_estimators=200, random_state=42))])
pipeline.fit(X_train, y_train)
prediction = pipeline.predict(X_test)
metrics = {"mae": float(mean_absolute_error(y_test, prediction)), "rmse": float(mean_squared_error(y_test, prediction) ** 0.5), "r2": float(r2_score(y_test, prediction)), "rows": len(data), "features": ["airline"], "limitation": "This dataset lacks reliable route, date, duration and stop data, so the model is only a coarse airline-based estimate."}
OUTPUT.mkdir(exist_ok=True)
joblib.dump(pipeline, OUTPUT / "price_model.joblib")
(OUTPUT / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
print(json.dumps(metrics, indent=2))
