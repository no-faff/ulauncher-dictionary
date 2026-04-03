import bisect
import hashlib
import json
import marshal
import os
import re
import subprocess
from urllib.parse import quote

from ulauncher.api.client.Extension import Extension
from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.shared.event import KeywordQueryEvent, ItemEnterEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.CopyToClipboardAction import CopyToClipboardAction
from ulauncher.api.shared.action.OpenUrlAction import OpenUrlAction
from ulauncher.api.shared.action.SetUserQueryAction import SetUserQueryAction
from ulauncher.api.shared.action.HideWindowAction import HideWindowAction
from ulauncher.api.shared.action.ExtensionCustomAction import ExtensionCustomAction

ICON = "images/icon.svg"
MAX_RESULTS = 15
DESC_LEN = 200
STARDICT_DIR = os.path.expanduser("~/.stardict/dic")
CONFIG_DIR = os.path.expanduser("~/.config/dicky")
CONFIG_FILE = os.path.join(CONFIG_DIR, "active_dict")
CACHE_DIR = os.path.expanduser("~/.cache/dicky")


def parse_ifo(ifo_path):
    """Read bookname and wordcount from a StarDict .ifo file."""
    bookname = ""
    wordcount = 0
    try:
        with open(ifo_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("bookname="):
                    bookname = line[9:].strip()
                elif line.startswith("wordcount="):
                    try:
                        wordcount = int(line[10:].strip())
                    except ValueError:
                        pass
    except OSError:
        pass
    return bookname, wordcount


_BOOKNAME_MAP = {
    "Urban Dictionary P1 (En-En)": "Urban Dictionary (A-Lag)",
    "Urban Dictionary P2 (En-En)": "Urban Dictionary (Lah-Z)",
}

_BOOKNAME_PATTERNS = [
    # Wiktionary from dictinfo.com: wikt-en-ALL-2025-10-05 etc.
    (re.compile(r'^wikt-en-en-[\d-]+$', re.I), "Wiktionary (English)"),
    (re.compile(r'^wikt-en-ALL-[\d-]+$', re.I), "Wiktionary (All languages)"),
    (re.compile(r'^wikt-en-Western-[\d-]+$', re.I), "Wiktionary (Western languages)"),
    (re.compile(r'^wikt-en-WGS-[\d-]+$', re.I), "Wiktionary (Western, Greek, Slavonic)"),
]


def prettify_bookname(name):
    """Clean up ugly technical booknames for display."""
    # Exact matches first
    if name in _BOOKNAME_MAP:
        return _BOOKNAME_MAP[name]
    # Pattern matches
    for pattern, display in _BOOKNAME_PATTERNS:
        if pattern.match(name):
            return display
    # Strip common dict.org prefix
    name = re.sub(r'^dictd_www\.dict\.org_', '', name)
    # Replace underscores with spaces
    name = name.replace('_', ' ')
    # Title-case if it looks like an identifier (all lower/upper, no spaces in original)
    if name == name.lower() or name == name.upper():
        name = name.upper() if len(name) <= 6 else name.title()
    return name


def detect_dictionaries():
    """Find all installed dictionaries. Returns list of (bookname, wordcount, ifo_path)."""
    dicts = []
    if not os.path.isdir(STARDICT_DIR):
        return dicts
    for dirpath, _, filenames in os.walk(STARDICT_DIR):
        for fname in filenames:
            if fname.endswith(".ifo"):
                ifo_path = os.path.join(dirpath, fname)
                bookname, wordcount = parse_ifo(ifo_path)
                if bookname:
                    dicts.append((bookname, wordcount, ifo_path))
    dicts.sort(key=lambda d: d[0].lower())
    return dicts


def _cache_path(active_dict):
    """Return the cache file path for a dictionary selection."""
    key = (active_dict or "_all_").encode("utf-8")
    h = hashlib.md5(key).hexdigest()[:12]
    return os.path.join(CACHE_DIR, f"headwords-{h}.dat")


def _idx_max_mtime(active_dict=None):
    """Return the newest mtime of any relevant .idx file."""
    newest = 0
    if not os.path.isdir(STARDICT_DIR):
        return newest
    for dirpath, _, filenames in os.walk(STARDICT_DIR):
        if active_dict:
            ifo_files = [f for f in filenames if f.endswith(".ifo")]
            if not ifo_files:
                continue
            bookname, _ = parse_ifo(os.path.join(dirpath, ifo_files[0]))
            if bookname.lower() != active_dict.lower():
                continue
        for fname in filenames:
            if fname.endswith(".idx"):
                mtime = os.path.getmtime(os.path.join(dirpath, fname))
                if mtime > newest:
                    newest = mtime
    return newest


def load_headwords(active_dict=None):
    """Load headwords from .idx files, using a marshal cache for speed.
    First load parses the binary .idx files (~36s for 8M words).
    Subsequent loads read from cache (~2-3s)."""
    cache = _cache_path(active_dict)

    # Try loading from cache
    if os.path.exists(cache):
        try:
            cache_mtime = os.path.getmtime(cache)
            if _idx_max_mtime(active_dict) <= cache_mtime:
                with open(cache, "rb") as f:
                    return marshal.load(f)
        except (OSError, ValueError):
            pass

    # Parse from .idx files
    words = []
    if not os.path.isdir(STARDICT_DIR):
        return words
    for dirpath, _, filenames in os.walk(STARDICT_DIR):
        if active_dict:
            ifo_files = [f for f in filenames if f.endswith(".ifo")]
            if not ifo_files:
                continue
            bookname, _ = parse_ifo(os.path.join(dirpath, ifo_files[0]))
            if bookname.lower() != active_dict.lower():
                continue
        for fname in filenames:
            if fname.endswith(".idx"):
                words.extend(parse_idx(os.path.join(dirpath, fname)))
    words = sorted(set(words), key=str.lower)

    # Save to cache
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache, "wb") as f:
            marshal.dump(words, f)
    except OSError:
        pass

    return words


def parse_idx(idx_path):
    """Parse a StarDict .idx file and return headwords."""
    words = []
    with open(idx_path, "rb") as f:
        data = f.read()
    i = 0
    try:
        while i < len(data):
            end = data.index(b"\x00", i)
            words.append(data[i:end].decode("utf-8", errors="replace"))
            i = end + 1 + 8  # null + 4-byte offset + 4-byte size
    except ValueError:
        pass
    return words


def prefix_search(query, headwords, headwords_lower, max_results=20):
    """Find headwords starting with query using bisect on sorted lowercase list."""
    prefix = query.lower()
    if not prefix:
        return []
    start = bisect.bisect_left(headwords_lower, prefix)
    results = []
    for i in range(start, len(headwords_lower)):
        if headwords_lower[i].startswith(prefix):
            results.append(headwords[i])
            if len(results) >= max_results:
                break
        else:
            break
    return results


def find_near_misses(word, word_set):
    """Generate edit-distance-1 variants and return those that are real words."""
    w = word.lower()
    candidates = set()
    for i in range(len(w) + 1):
        for c in "abcdefghijklmnopqrstuvwxyz":
            candidates.add(w[:i] + c + w[i:])
    for i in range(len(w)):
        candidates.add(w[:i] + w[i + 1:])
        for c in "abcdefghijklmnopqrstuvwxyz":
            if c != w[i]:
                candidates.add(w[:i] + c + w[i + 1:])
    for i in range(len(w) - 1):
        candidates.add(w[:i] + w[i + 1] + w[i] + w[i + 2:])
    candidates.discard(w)
    return [c for c in candidates if c in word_set]


def sdcv_json(word, active_dict=None):
    """Fetch definition for a word via sdcv (case-insensitive exact match)."""
    base_cmd = ["sdcv", "-n", "-j", "--utf8-output"]
    if active_dict:
        base_cmd += ["-u", active_dict]
    try:
        result = subprocess.run(
            base_cmd + ["-e", word],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        return None
    entries = []
    try:
        if result.stdout.strip():
            entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        pass
    if entries:
        return entries
    # sdcv -e is case-sensitive; retry without -e and filter
    try:
        result = subprocess.run(
            base_cmd + [word],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        return None
    if not result.stdout.strip():
        return []
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [e for e in entries if e.get("word", "").lower() == word.lower()]


_GCIDE_ACCENTS = {
    '"': {  # diaeresis
        'a': 'ä', 'e': 'ë', 'i': 'ï', 'o': 'ö', 'u': 'ü',
        'A': 'Ä', 'E': 'Ë', 'I': 'Ï', 'O': 'Ö', 'U': 'Ü',
    },
    '^': {  # circumflex
        'a': 'â', 'e': 'ê', 'i': 'î', 'o': 'ô', 'u': 'û',
        'A': 'Â', 'E': 'Ê', 'I': 'Î', 'O': 'Ô', 'U': 'Û',
    },
    '~': {  # tilde
        'a': 'ã', 'n': 'ñ', 'o': 'õ',
        'A': 'Ã', 'N': 'Ñ', 'O': 'Õ',
    },
    '=': {  # macron
        'a': 'ā', 'e': 'ē', 'i': 'ī', 'o': 'ō', 'u': 'ū',
        'A': 'Ā', 'E': 'Ē', 'I': 'Ī', 'O': 'Ō', 'U': 'Ū',
    },
}


def _gcide_accent_replace(m):
    mod, char = m.group(1), m.group(2)
    return _GCIDE_ACCENTS.get(mod, {}).get(char, char)


def clean_definition(text):
    """Strip wav filenames, UK/US audio labels, metadata, markup and examples."""
    # GCIDE accent markup: ["o] -> ö, [^e] -> ê, etc.
    text = re.sub(r'\[(["^~=])([a-zA-Z])\]', _gcide_accent_replace, text)
    # GCIDE cross-references: {Pisces} -> Pisces
    text = re.sub(r'\{([^}]+)\}', r'\1', text)
    # GCIDE date/source markers: [1913 Webster], [PJC], etc.
    text = re.sub(r'\[\d{4} Webster\]', '', text)
    text = re.sub(r'\[PJC\]', '', text)
    # GCIDE backtick quotes: ``word'' -> "word"
    text = text.replace("``", "\u201c").replace("''", "\u201d")
    # Cambridge grammar labels: wrap in brackets so they read as context
    text = re.sub(
        r"(countable or uncountable|countable|uncountable"
        r"|only singular|only plural|usually singular|usually plural"
        r"|not comparable)\s+",
        r"(\1) ", text
    )
    # Concise Oxford: strip ■ markers and ↘ sub-definition arrows
    text = text.replace("\u25a0", "").replace("\u2198", "")
    # Replace non-breaking spaces with regular spaces
    text = text.replace("\xa0", " ")
    # Strip <E > and similar markup tags
    text = re.sub(r"<[A-Z][^>]*>", "", text)
    # Strip wav filenames and preceding UK/US labels
    text = re.sub(r"\s*(?:UK|US)\s+\S+\.wav", "", text)
    # Catch any remaining wav references
    text = re.sub(r"\S+\.wav", "", text)
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip metadata lines
        if stripped.startswith("Thesaurus+:") or stripped.startswith("Derived:"):
            continue
        # Skip example sentences (bullet points)
        if stripped and stripped[0] in "\u2022\u2219*":
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def extract_header(definition_text):
    """Pull pronunciation and word class from the definition start."""
    lines = [l for l in definition_text.strip().splitlines() if l.strip()]
    if not lines:
        return "", ""

    pronunciation = ""
    for line in lines[:3]:
        match = re.search(r"/[^/]+/", line)
        if match:
            pronunciation = match.group(0)
            break

    word_class = ""
    class_pattern = re.compile(
        r"\b(noun|verb|adjective|adverb|pronoun|preposition|conjunction"
        r"|interjection|transitive verb|intransitive verb|prefix"
        r"|suffix|combining form)\b"
    )
    for line in lines[:5]:
        match = class_pattern.search(line.lower())
        if match:
            word_class = match.group(1)
            break

    return pronunciation, word_class


def extract_definitions(definition_text, limit=7):
    """Pull definitions from the text. Tries numbered defs first,
    then falls back to unnumbered non-empty lines after the header."""
    lines = definition_text.splitlines()
    vote_pattern = re.compile(r"^\(\d+ up, \d+ down\)$")
    # Match "1. ..." or "1) ..." or "1》..." numbering styles
    num_pattern = re.compile(r"^\s*(\d+)[.)\u300b]\s*(.+)")
    # GCIDE markers: [1913 Webster], [PJC], [Obs.], etc. - but not [with negative]
    marker_pattern = re.compile(r"^\s*\[\d{4}\s|^\s*\[PJC\]|^\s*\[Obs")

    # Try numbered definitions first
    defs = []
    seen = set()
    i = 0
    while i < len(lines):
        match = num_pattern.match(lines[i])
        if match:
            text = match.group(2).strip()
            # Urban Dictionary: vote count on numbered line, definition on next
            if vote_pattern.match(text):
                for j in range(i + 1, len(lines)):
                    next_line = lines[j].strip()
                    if next_line:
                        text = next_line
                        i = j
                        break
                else:
                    i += 1
                    continue
            else:
                # Collect indented continuation lines (GCIDE multi-line defs)
                pos_label = re.compile(
                    r"^\s*(noun|verb|adjective|adverb|pronoun|preposition"
                    r"|conjunction|interjection)\s*$", re.I
                )
                j = i + 1
                while j < len(lines):
                    cont = lines[j]
                    if not cont.strip():
                        break
                    if marker_pattern.match(cont):
                        break
                    if num_pattern.match(cont):
                        break
                    if pos_label.match(cont):
                        break
                    if not cont[0].isspace():
                        break
                    text += " " + cont.strip()
                    j += 1
                i = j
            # Deduplicate (GCIDE sometimes repeats definitions)
            dedup_key = text[:60].lower()
            if dedup_key not in seen:
                seen.add(dedup_key)
                defs.append(text)
            if len(defs) >= limit:
                break
        else:
            i += 1
    if defs:
        return defs

    # No numbered defs - treat non-empty lines after the first as definitions.
    # Skip part-of-speech labels and roman numerals.
    skip = re.compile(
        r"^(n\.|v\.|adj\.|adv\.|prep\.|conj\.|pron\.|interj\."
        r"|I{1,3}V?|VI{0,3}|noun|verb|adjective|adverb)$"
    )
    non_empty = [l.strip() for l in lines if l.strip()]
    if len(non_empty) > 1:
        result = [l for l in non_empty[1:] if not skip.match(l)]
        return result[:limit]
    return []


def extract_origin(definition_text):
    """Pull the ORIGIN line if present."""
    for line in definition_text.splitlines():
        if line.startswith("ORIGIN:"):
            return line
    return ""


def read_active_dict():
    """Read the active dictionary from the config file. Returns None if unset."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            name = f.read().strip()
            return name if name else None
    except OSError:
        return None


def write_active_dict(bookname):
    """Write the active dictionary choice to config. Empty string means all."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(bookname)


def config_mtime():
    """Return mtime of the config file, or 0 if it doesn't exist."""
    try:
        return os.path.getmtime(CONFIG_FILE)
    except OSError:
        return 0


def preview_definition(word, active_dict=None):
    """Get a clean first-definition preview for a suggestion item."""
    entries = sdcv_json(word, active_dict)
    if not entries:
        return ""
    defn = clean_definition(entries[0].get("definition", ""))
    defs = extract_definitions(defn, limit=1)
    if defs:
        return defs[0][:DESC_LEN]
    # Fallback: collapse to single line
    text = " ".join(defn.split())
    return text[:DESC_LEN] if text else ""


class DictionaryExtension(Extension):
    def __init__(self):
        super().__init__()
        self.active_dict = read_active_dict()
        self._config_mtime = config_mtime()
        self.headwords = []
        self.headwords_lower = []
        self.word_set = set()
        self._headwords_stale = True
        self.auto_select_if_needed()
        self.subscribe(KeywordQueryEvent, QueryListener())
        self.subscribe(ItemEnterEvent, DictChooserListener())

    def reload_headwords(self):
        """Reload headwords from disk for the active dictionary."""
        self.active_dict = read_active_dict()
        self._config_mtime = config_mtime()
        self.headwords = load_headwords(self.active_dict)
        self.headwords_lower = [w.lower() for w in self.headwords]
        self.word_set = {w.lower() for w in self.headwords}

    def check_config_changed(self):
        """Reload headwords if the config file was modified externally."""
        mtime = config_mtime()
        if mtime != self._config_mtime:
            self.reload_headwords()

    def auto_select_if_needed(self):
        """If no dictionary is selected and only one is installed, select it."""
        if self.active_dict is not None:
            return
        dicts = detect_dictionaries()
        if len(dicts) == 1:
            write_active_dict(dicts[0][0])
            self.reload_headwords()

    def build_dict_list(self):
        """Build the dictionary chooser result list."""
        dicts = detect_dictionaries()
        if not dicts:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=ICON,
                    name="No dictionaries installed",
                    description="Place StarDict files in ~/.stardict/dic/",
                    on_enter=HideWindowAction(),
                )
            ])

        items = []

        # Prompt if no dictionary selected yet
        if self.active_dict is None:
            items.append(ExtensionResultItem(
                icon=ICON,
                name="Select a dictionary to get started",
                description="",
                highlightable=False,
                on_enter=HideWindowAction(),
            ))

        # Active dictionary first, then the rest alphabetically
        dicts.sort(key=lambda d: (
            not (self.active_dict and d[0].lower() == self.active_dict.lower()),
            d[0].lower(),
        ))
        for bookname, wordcount, _ in dicts:
            active = (self.active_dict and
                      bookname.lower() == self.active_dict.lower())
            display_name = prettify_bookname(bookname)
            slow_note = " (first search may be slow)" if wordcount > 1000000 else ""
            if active:
                name = f"\u2713 {display_name}"
                desc = f"Active, {wordcount:,} words" if wordcount else "Active"
            else:
                name = display_name
                desc = f"{wordcount:,} words{slow_note}" if wordcount else ""
            items.append(ExtensionResultItem(
                icon=ICON,
                name=name,
                description=desc,
                highlightable=False,
                on_enter=ExtensionCustomAction(
                    {"action": "set_dict", "bookname": bookname},
                    keep_app_open=True,
                ),
            ))

        return RenderResultListAction(items[:MAX_RESULTS])


class DictChooserListener(EventListener):
    def on_event(self, event, extension):
        data = event.get_data()
        if not isinstance(data, dict) or data.get("action") != "set_dict":
            return
        bookname = data.get("bookname", "")
        write_active_dict(bookname)
        # Update state for the list display, but defer headword loading
        # until the user actually searches. This keeps the chooser instant
        # even for dictionaries with millions of headwords.
        extension.active_dict = bookname if bookname else None
        extension._config_mtime = config_mtime()
        extension._headwords_stale = True
        return extension.build_dict_list()


class QueryListener(EventListener):
    def on_event(self, event, extension):
        extension.check_config_changed()
        keyword = event.get_keyword()
        word = (event.get_argument() or "").strip()

        # Empty query: show dictionary chooser immediately (no headword load)
        if not word:
            return extension.build_dict_list()

        # If no dictionary selected, redirect to chooser
        if extension.active_dict is None:
            return extension.build_dict_list()

        # Load headwords if stale (deferred from dictionary switch)
        if getattr(extension, '_headwords_stale', False):
            extension.headwords = load_headwords(extension.active_dict)
            extension.headwords_lower = [w.lower() for w in extension.headwords]
            extension.word_set = {w.lower() for w in extension.headwords}
            extension._headwords_stale = False

        # Try exact match
        entries = sdcv_json(word, extension.active_dict)

        if entries is None:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=ICON,
                    name="sdcv not found",
                    description="Install sdcv: sudo dnf install sdcv",
                    on_enter=HideWindowAction(),
                )
            ])

        if entries:
            return self.show_definition(keyword, word, entries, extension)

        # No exact match - prefix search + near misses
        return self.show_suggestions(keyword, word, extension)

    def show_definition(self, keyword, word, entries, extension):
        items = []
        for entry in entries:
            raw_defn = entry.get("definition", "")
            defn = clean_definition(raw_defn)
            matched_word = entry.get("word", word)

            pronunciation, word_class = extract_header(defn)
            numbered_defs = extract_definitions(defn)
            origin = extract_origin(defn)

            title_parts = [matched_word]
            if pronunciation:
                title_parts.append(pronunciation)
            if word_class:
                title_parts.append(f"({word_class})")
            title = "  ".join(title_parts)

            if numbered_defs:
                # Header row
                items.append(ExtensionResultItem(
                    icon=ICON,
                    name=title,
                    description="",
                    highlightable=False,
                    on_enter=CopyToClipboardAction(defn.strip()),
                ))
                for i, d in enumerate(numbered_defs):
                    # Split long definitions across name and description
                    name_text = f"{i+1}. {d}"
                    desc_text = ""
                    if len(name_text) > 80:
                        # Find a word boundary near 80 chars
                        cut = name_text.rfind(" ", 0, 80)
                        if cut > 20:
                            desc_text = name_text[cut + 1:]
                            name_text = name_text[:cut]
                    items.append(ExtensionResultItem(
                        icon=ICON,
                        name=name_text,
                        description=desc_text,
                        highlightable=False,
                        on_enter=CopyToClipboardAction(d),
                    ))
            else:
                desc = " ".join(defn.split())
                if len(desc) > DESC_LEN:
                    desc = desc[:DESC_LEN] + "..."
                items.append(ExtensionResultItem(
                    icon=ICON,
                    name=title,
                    description=desc,
                    highlightable=False,
                    on_enter=CopyToClipboardAction(defn.strip()),
                ))

            if origin:
                items.append(ExtensionResultItem(
                    icon=ICON,
                    name="Origin",
                    description=origin.replace("ORIGIN: ", ""),
                    highlightable=False,
                    on_enter=CopyToClipboardAction(origin),
                ))

            items.append(ExtensionResultItem(
                icon=ICON,
                name=f"Open '{matched_word}' on Wiktionary",
                description="View full entry in browser",
                highlightable=False,
                on_enter=OpenUrlAction(
                    f"https://en.wiktionary.org/wiki/{quote(matched_word)}"
                ),
            ))

        # Also show prefix matches below the definition
        prefix_matches = prefix_search(
            word, extension.headwords, extension.headwords_lower
        )
        seen = {word.lower()}
        for suggestion in prefix_matches:
            if len(items) >= MAX_RESULTS:
                break
            if suggestion.lower() in seen:
                continue
            seen.add(suggestion.lower())
            preview = preview_definition(suggestion, extension.active_dict)
            items.append(ExtensionResultItem(
                icon=ICON,
                name=suggestion,
                description=preview,
                highlightable=False,
                on_enter=SetUserQueryAction(f"{keyword} {suggestion}"),
            ))

        return RenderResultListAction(items[:MAX_RESULTS])

    def show_suggestions(self, keyword, word, extension):
        # Prefix matches
        prefix_matches = prefix_search(
            word, extension.headwords, extension.headwords_lower
        )

        # Edit-distance-1 near misses (typo correction)
        near_misses = find_near_misses(word, extension.word_set)

        # Near misses first (typo corrections), then prefix matches
        seen = set()
        ordered = []
        for w in near_misses:
            wl = w.lower()
            if wl not in seen:
                seen.add(wl)
                ordered.append(w)
        for w in prefix_matches:
            wl = w.lower()
            if wl not in seen:
                seen.add(wl)
                ordered.append(w)

        if not ordered:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon=ICON,
                    name=f"No results for '{word}'",
                    description="Word not found in any dictionary",
                    on_enter=HideWindowAction(),
                )
            ])

        items = [
            ExtensionResultItem(
                icon=ICON,
                name=f"No exact match for '{word}'",
                description="Did you mean:",
                highlightable=False,
                on_enter=HideWindowAction(),
            )
        ]

        for suggestion in ordered[:MAX_RESULTS - 1]:
            preview = preview_definition(suggestion, extension.active_dict)
            items.append(ExtensionResultItem(
                icon=ICON,
                name=suggestion,
                description=preview,
                highlightable=False,
                on_enter=SetUserQueryAction(f"{keyword} {suggestion}"),
            ))

        return RenderResultListAction(items[:MAX_RESULTS])


if __name__ == "__main__":
    DictionaryExtension().run()
