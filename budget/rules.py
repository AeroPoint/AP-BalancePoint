"""Merchant name cleanup and the rule engine that renames + categorizes transactions."""
import re
from dataclasses import dataclass

from . import seed

# US Bank's Name column is fixed width: 22 chars merchant, 13 chars city, 2 char state.
USBANK_FIXED = re.compile(r"^(.{22}) (.{13}) ([A-Za-z]{2})$")
# Payment processors that prefix the real merchant name.
PROCESSOR_PREFIX = re.compile(
    r"^(SQ ?\*|TST ?\*|SP |PAYPAL ?\*|PY ?\*|CKE ?\*|FH ?\*|WF ?\*|ICP ?\*|GLOSS ?\*|CPI ?\*|"
    r"BCF |SSA |RTA |DD ?\*|EB ?\*|POS PURCHASE |DEBIT PURCHASE )"
)
SOURCE_RANK = {"user": 0, "config": 1, "excel": 2, "builtin": 3}


def normalize(text):
    return " ".join(str(text or "").upper().split())


def merchant_part(raw):
    """Strip the city/state columns off a US Bank description."""
    raw = str(raw or "").rstrip()
    m = USBANK_FIXED.match(raw)
    if m:
        return m.group(1).strip()
    chunks = re.split(r"\s{2,}", raw.strip())  # same layout with trimmed padding
    return chunks[0] if len(chunks) >= 2 else raw.strip()


def _is_noise_token(tok):
    digits = sum(ch.isdigit() for ch in tok)
    if re.fullmatch(r"[\d\-/.()]{3,}", tok):  # store numbers, phone numbers
        return True
    return len(tok) >= 5 and digits >= 2  # reference codes like F31767, P3BEB3A850


def merchant_key(raw):
    """A stable, uppercase key for a merchant, e.g. 'SAFEWAY #1234  SPRINGFIELD IL' -> 'SAFEWAY'.

    Used to suggest dictionary patterns and to group raw names together.
    """
    s = normalize(merchant_part(raw))
    s = PROCESSOR_PREFIX.sub("", s)
    if "*" in s:
        head, _, tail = s.partition("*")
        s = head if len(head.strip()) >= 3 else tail
    s = re.sub(r"#\s*\S*", " ", s)
    tokens = [t for t in s.split() if re.search(r"[A-Z0-9]", t) and not _is_noise_token(t)]
    return " ".join(tokens) or normalize(raw)


def suggest_name(raw):
    """Readable fallback name when no dictionary entry matches."""
    words = []
    for w in merchant_key(raw).split():
        if len(w) <= 3 and w.isalpha() and w not in {"THE", "AND", "INN", "BAR", "CAR", "PET", "ZOO"}:
            words.append(w)  # likely an acronym: BWW, ADT, CPW
        else:
            words.append(w.capitalize())
    name = " ".join(words)
    return re.sub(r"'S\b", "'s", name).replace(".Com", ".com")


def compile_pattern(pattern):
    if pattern.startswith("re:"):
        return re.compile(pattern[3:], re.IGNORECASE)
    return re.compile(r"(?<![A-Z0-9])" + re.escape(normalize(pattern)) + r"(?![A-Z0-9])")


@dataclass
class Match:
    name: str
    category_id: int | None
    source: str  # rule | mcc | none


class RuleEngine:
    def __init__(self, conn):
        self.categories = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}
        compiled = []
        for r in conn.execute("SELECT * FROM rules"):
            try:
                rx = compile_pattern(r["pattern"])
            except re.error:
                continue
            # Most specific (longest) pattern first; user rules beat Excel beat built-in on ties.
            compiled.append(((-len(r["pattern"]), SOURCE_RANK.get(r["source"], 3)), rx, dict(r)))
        compiled.sort(key=lambda c: c[0])
        self.raw_rules = [(rx, r) for _, rx, r in compiled if r["match_on"] == "raw"]
        self.name_rules = [(rx, r) for _, rx, r in compiled if r["match_on"] == "name" and r["category_id"]]
        # "Always categorize <merchant> as X" set by you wins over everything else.
        self.pinned = {
            normalize(r["pattern"]): r["category_id"]
            for _, r in self.name_rules
            if r["source"] == "user" and not r["pattern"].startswith("re:")
        }
        # Friendly name -> category, so "Groc Mart" typed in the old spreadsheet still categorizes.
        self.name_to_category = {}
        for _, r in self.raw_rules:
            if r["rename_to"] and r["category_id"]:
                self.name_to_category.setdefault(normalize(r["rename_to"]), r["category_id"])

    def resolve(self, raw, mcc=None, fixed_name=None):
        text = normalize(raw)
        # Patterns are matched against the raw text and against its cleaned key, so an entry
        # like "SAFEWAY FUEL" still matches "SAFEWAY #1234 FUEL SPRINGFIELD IL".
        key = merchant_key(raw)
        name, raw_category = fixed_name, None
        for rx, r in self.raw_rules:
            wants_name = name is None and r["rename_to"]
            wants_category = raw_category is None and r["category_id"]
            if (wants_name or wants_category) and (rx.search(text) or rx.search(key)):
                if wants_name:
                    name = r["rename_to"]
                if wants_category:
                    raw_category = r["category_id"]
            if name is not None and raw_category is not None:
                break
        if name is None:
            name = suggest_name(raw)

        upper = normalize(name)
        category_id = (
            self.pinned.get(upper)
            or raw_category
            or self.name_to_category.get(upper)
            or next((r["category_id"] for rx, r in self.name_rules if rx.search(upper)), None)
        )
        if category_id is not None:
            return Match(name, category_id, "rule")

        category_id = self.categories.get(seed.mcc_category(mcc))
        if category_id is not None:
            return Match(name, category_id, "mcc")
        return Match(name, None, "none")


def reapply(conn, where="1=1", params=()):
    """Re-run rules over existing transactions, leaving hand-edited names/categories alone
    (categories picked in the app or hand-sorted in the old spreadsheet).

    Returns the number of transactions that changed.
    """
    engine = RuleEngine(conn)
    rows = conn.execute(
        f"SELECT id, raw_description, mcc, name, name_locked, category_id, category_source "
        f"FROM transactions WHERE {where}",
        params,
    ).fetchall()
    changed = 0
    for t in rows:
        m = engine.resolve(t["raw_description"], t["mcc"], t["name"] if t["name_locked"] else None)
        name = t["name"] if t["name_locked"] else m.name
        if t["category_source"] in ("manual", "sheet"):
            category_id, source = t["category_id"], t["category_source"]
        else:
            category_id, source = m.category_id, m.source
        if (name, category_id, source) != (t["name"], t["category_id"], t["category_source"]):
            conn.execute(
                "UPDATE transactions SET name = ?, category_id = ?, category_source = ? WHERE id = ?",
                (name, category_id, source, t["id"]),
            )
            changed += 1
    return changed
