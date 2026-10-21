from .futures import score_futures
from .listings import score_listings
from .memes import score_memes
from .spot_alts import score_spot_alts
from .stables import score_stables
from .whales import score_whales

__all__ = [
    "score_spot_alts",
    "score_futures",
    "score_memes",
    "score_stables",
    "score_listings",
    "score_whales",
]
