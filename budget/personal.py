"""Personal settings kept outside the code, in data/personal.toml (git-ignored).

Anything specific to one household lives there: local merchants, extra categories and keywords,
and how to read an old budget spreadsheet. See personal.example.toml for the format.
"""
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CATEGORY_KINDS = ("expense", "income", "transfer")


class PersonalConfigError(ValueError):
    pass


@dataclass
class Personal:
    path: Path | None = None
    categories: list = field(default_factory=list)   # [(name, kind)]
    merchants: list = field(default_factory=list)    # [(match, name or None, category or None)]
    keywords: list = field(default_factory=list)     # [(keyword, category)]
    spreadsheet: dict = field(default_factory=dict)  # raw [spreadsheet] table, read by excel_import


def load(path):
    """Read personal.toml; a missing file just means no personal settings."""
    path = Path(path)
    if not path.is_file():
        return Personal()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        categories = []
        for entry in raw.get("category", []):
            kind = entry.get("kind", "expense")
            if kind not in CATEGORY_KINDS:
                raise PersonalConfigError(
                    f"{path}: category {entry.get('name')!r} has kind {kind!r}; use expense, income or transfer."
                )
            categories.append((entry["name"], kind))
        merchants = [
            (entry["match"], entry.get("name"), entry.get("category"))
            for entry in raw.get("merchant", [])
            if entry.get("name") or entry.get("category")
        ]
        keywords = [(str(k), str(v)) for k, v in raw.get("keywords", {}).items()]
    except tomllib.TOMLDecodeError as exc:
        raise PersonalConfigError(f"{path} isn't valid TOML: {exc}") from exc
    except KeyError as exc:
        raise PersonalConfigError(f"{path}: an entry is missing its {exc} value.") from exc
    return Personal(path, categories, merchants, keywords, raw.get("spreadsheet", {}))
