"""Sentiment: FinBERT (open weights, ProsusAI/finbert) when enabled, else a finance lexicon."""
import os
import re

POS = set("""beat beats surge surges jump jumps soar soars record rally rallies gain gains growth upgrade upgrades
profit profits approval approved wins win bags secures awarded outperform strong robust rises rise higher boost
expands expansion bullish buyback dividend clearance accretive improves improved""".split())
NEG = set("""miss misses plunge plunges fall falls slump slumps drop drops decline declines loss losses downgrade
downgrades ban banned penalty fine fined probe fraud default weak cut cuts lower warning bearish resign resigns
pledge invoked raid scam delay recall observations halt crash concern concerns slashes slashed""".split())
NEGATORS = {"no", "not", "without", "fails", "failed"}

_finbert = None


def _load_finbert():
    global _finbert
    if _finbert is None:
        from transformers import pipeline  # optional heavy dependency
        _finbert = pipeline("text-classification", model="ProsusAI/finbert", top_k=None, truncation=True)
    return _finbert


def lexicon_scores(text):
    toks = re.findall(r"[a-z']+", text.lower())
    p = n = 0.0
    for i, t in enumerate(toks):
        flip = i > 0 and toks[i - 1] in NEGATORS
        if t in POS:
            n, p = (n + 1, p) if flip else (n, p + 1)
        elif t in NEG:
            p, n = (p + 1, n) if flip else (p, n + 1)
    tot = p + n
    if tot == 0:
        return {"positive": 0.1, "negative": 0.1, "neutral": 0.8}
    conf = min(0.95, 0.5 + 0.15 * tot)
    return {"positive": conf * p / tot, "negative": conf * n / tot, "neutral": 1 - conf}


def analyse(texts):
    """Return list of dicts: probs + score (= P(pos) - P(neg)) and engine used."""
    if os.environ.get("USE_FINBERT") == "1":
        try:
            out = _load_finbert()(list(texts), batch_size=16)
            res = []
            for o in out:
                d = {x["label"].lower(): x["score"] for x in o}
                res.append({**d, "score": d.get("positive", 0) - d.get("negative", 0), "engine": "finbert"})
            return res
        except Exception as e:  # noqa: BLE001 - fall back silently but visibly
            print("FinBERT unavailable, using lexicon:", e)
    res = []
    for t in texts:
        d = lexicon_scores(t)
        res.append({**d, "score": d["positive"] - d["negative"], "engine": "lexicon"})
    return res
