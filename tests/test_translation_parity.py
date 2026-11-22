"""Translation parity for the BattMind texts/ and translations/ files.

Covers every language file in full. The partial checks in test_automation_test.py (ui.automation_test.*
placeholders) and test_init.py (what_if service fields) stay as they are.
"""

import json
import re
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "battmind"
LANGUAGE_FILES = {"da.json", "de.json", "en.json", "pl.json", "uk.json"}
MIN_TEXT_KEYS = 700  # the real texts/ files have 732 keys each
MIN_TRANSLATION_KEYS = 600  # the real translations/ files have 631 keys each
PLACEHOLDER = re.compile(r"\{([a-zA-Z_0-9]+)\}")
REFERENCE = "[%key:"


def _flatten(value, prefix: str = "") -> dict[str, object]:
    if isinstance(value, dict):
        children = value.items()
    elif isinstance(value, list):
        children = ((str(index), item) for index, item in enumerate(value))
    else:
        return {prefix: value}
    flat: dict[str, object] = {}
    for key, child in children:
        flat.update(_flatten(child, f"{prefix}.{key}" if prefix else str(key)))
    return flat


def _load(path: Path) -> dict[str, object]:
    return _flatten(json.loads(path.read_text(encoding="utf-8")))


def _language_files(directory: Path) -> dict[str, Path]:
    files = {path.name: path for path in directory.glob("*.json")}
    assert set(files) == LANGUAGE_FILES, directory
    return files


def _comparable_placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def _placeholder_mismatches(reference: dict[str, object], values: dict[str, object]) -> list[str]:
    problems = []
    for key, reference_value in reference.items():
        value = values.get(key)
        if not isinstance(reference_value, str) or not isinstance(value, str):
            continue
        if REFERENCE in reference_value or REFERENCE in value:
            continue  # "[%key:...%]" points at another string, which is checked there
        expected = _comparable_placeholders(reference_value)
        actual = _comparable_placeholders(value)
        if expected != actual:
            problems.append(f"{key}: {sorted(expected)} != {sorted(actual)}")
    return problems


def test_flatten_uses_dots_and_list_indexes() -> None:
    assert _flatten({"a": {"b": [{"c": "x"}, "y"]}, "d": "z"}) == {"a.b.0.c": "x", "a.b.1": "y", "d": "z"}


def test_the_checker_finds_a_lost_or_added_placeholder() -> None:
    assert _placeholder_mismatches({"a": "{x} kWh"}, {"a": "kWh"}) == ["a: ['x'] != []"]
    assert _placeholder_mismatches({"a": "kWh"}, {"a": "{valuta_kr} kWh"}) == ["a: [] != ['valuta_kr']"]
    assert _placeholder_mismatches({"a": "{x}"}, {"a": "[%key:other%]"}) == []


def test_the_language_files_of_texts_and_translations_are_the_expected_set() -> None:
    texts = _language_files(COMPONENT / "texts")
    translations = _language_files(COMPONENT / "translations")
    assert set(texts) == set(translations) == LANGUAGE_FILES


def test_texts_have_enough_keys_in_every_language() -> None:
    for name, path in _language_files(COMPONENT / "texts").items():
        assert len(_load(path)) >= MIN_TEXT_KEYS, name


def test_translations_have_enough_keys_in_every_language() -> None:
    for name, path in _language_files(COMPONENT / "translations").items():
        assert len(_load(path)) >= MIN_TRANSLATION_KEYS, name


def test_texts_have_the_same_keys_in_every_language() -> None:
    files = _language_files(COMPONENT / "texts")
    english = set(_load(files["en.json"]))
    for name, path in files.items():
        keys = set(_load(path))
        assert keys == english, f"{name}: missing {sorted(english - keys)[:10]}, extra {sorted(keys - english)[:10]}"


def test_texts_have_the_same_placeholders_in_every_language() -> None:
    files = _language_files(COMPONENT / "texts")
    english = _load(files["en.json"])
    for name, path in files.items():
        assert _placeholder_mismatches(english, _load(path)) == [], name


def test_translations_have_the_same_keys_as_strings_json() -> None:
    strings = set(_load(COMPONENT / "strings.json"))
    for name, path in _language_files(COMPONENT / "translations").items():
        keys = set(_load(path))
        assert keys == strings, f"{name}: missing {sorted(strings - keys)[:10]}, extra {sorted(keys - strings)[:10]}"


def test_translations_have_the_same_placeholders_as_english() -> None:
    # en.json is compared with its source strings.json, every other language with en.json.
    strings = _load(COMPONENT / "strings.json")
    english = _load(COMPONENT / "translations" / "en.json")
    for name, path in _language_files(COMPONENT / "translations").items():
        reference = strings if name == "en.json" else english
        assert _placeholder_mismatches(reference, _load(path)) == [], name
