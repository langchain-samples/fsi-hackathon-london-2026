"""Settings shared by the control tower: the model and the FX rates used to compare amounts in GBP."""
import os

from dotenv import load_dotenv

from control_tower.loader import REPO_ROOT

load_dotenv(REPO_ROOT / ".env")

MODEL = os.environ.get("MODEL_NAME", "openai:gpt-6-luna")

# Illustrative, fixed rates for the synthetic data only.
GBP_PER = {"GBP": 1.0, "EUR": 0.85, "USD": 0.78}


def to_gbp(amount: float, currency: str) -> float:
    return round(amount * GBP_PER.get(currency.upper(), 1.0), 2)
