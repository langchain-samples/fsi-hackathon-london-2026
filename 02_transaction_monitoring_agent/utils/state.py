from dataclasses import dataclass

OCT_19 = "2026-10-19"  # Module 3's transactions start here

@dataclass
class MyAgentContext:
    """Information we pass in with every run. Tools can read it; the model can't."""
    user_id: str = ""

    confidence_threshold: float = 0.7       # below this, a human decides
    checked_confidence: float = 0.5         # what a failed check lowers the confidence to
    grey_zone: tuple[int, int] = (30, 70)   # risk scores from 30 to 69 are "unusual, but could be legitimate"
