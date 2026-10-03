"""Tag normalization and classification. last.fm tags are folksonomy: next to genres they
hold years, decades, places and personal labels ("seen live"). Only 'genre' tags feed the
genre profile; year tags are a fallback release year for albums."""
import re

from .ingest import key

YEAR = re.compile(r"^(19[0-9]{2}|20[0-9]{2})$")
DECADE = re.compile(r"^(?:19|20)?[0-9]0'?s$")

PLACES = {
    "finnish", "finland", "suomi", "suomeksi", "swedish", "sweden", "norwegian", "norway", "danish",
    "denmark", "icelandic", "iceland", "scandinavian", "nordic", "british", "uk", "english", "scottish",
    "irish", "welsh", "american", "usa", "canadian", "australian", "german", "germany", "french", "france",
    "japanese", "japan", "korean", "brazilian", "italian", "spanish", "dutch", "belgian", "russian",
    "polish", "estonian", "mexican", "african",
}
# Personal or descriptive labels that say nothing about genre.
OTHER = {
    "seen live", "favorites", "favourites", "favorite", "favourite", "my favorite", "my favorites",
    "albums i own", "love", "loved", "beautiful", "awesome", "amazing", "cool", "good", "great",
    "best", "check out", "spotify", "under 2000 listeners", "all", "music", "albums", "album",
    "female vocalists", "male vocalists", "female vocalist", "male vocalist", "female", "male",
    "favorite albums", "favourite albums", "best of", "classic", "masterpiece", "chill", "sad",
    "melancholy", "mellow", "happy", "fun", "party", "sexy", "epic", "lyrics",
    "vinyl", "cd", "owned", "wishlist", "to listen", "fip",
}


def normalize(name: str) -> str:
    return key(name).replace("_", " ").strip(" -")


def classify(name: str) -> str:
    n = normalize(name)
    if YEAR.match(n):
        return "year"
    if DECADE.match(n):
        return "decade"
    if n in PLACES:
        return "place"
    if n in OTHER or len(n) < 2:
        return "other"
    return "genre"
