"""Batch scoring (US-204): scores/scores per customer and scores/request_scores per open return,
from the trained risk models (ml.risk) and the as-of feature tables. It never reads the ground
truth (rule 12): only the optional FAIR-02/03 evaluation of `python -m ml.scoring --truth` does.
"""
