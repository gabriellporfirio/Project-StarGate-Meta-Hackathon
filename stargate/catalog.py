"""Validator for the M3 model catalog (authoritative; no JSON schema)."""
from __future__ import annotations

SCHEMA_VERSION = "catalog-v1"

TOP_LEVEL_KEYS = frozenset({"schema_version", "models"})
ENTRY_KEYS = frozenset({
    "id", "source", "revision", "files", "format", "runtime", "abi",
    "licence", "provenance", "estimates",
})
ESTIMABLE_FIELDS = frozenset({
    "source", "revision", "format", "runtime", "abi", "licence",
})
# Fields whose values come from the model page or licence text. A field
# listed in estimates is a planning estimate, so it cannot also be one
# of these (M4 section 7).
SOURCED_FIELDS = frozenset({
    "source", "revision", "format", "licence",
})
FILE_KEYS = frozenset({"name", "bytes"})
PROVENANCE_KEYS = frozenset({"kind", "url", "date", "note"})
PROVENANCE_KINDS = ("sourced", "synthetic")


def _is_non_empty_str(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_str_or_null(value: object) -> bool:
    return value is None or isinstance(value, str)


def validate_entry(entry: object) -> list:
    """Validate one catalog entry; returns a list of error strings."""
    if not isinstance(entry, dict):
        return ["entry must be an object"]
    errors = []
    for key in entry:
        if key not in ENTRY_KEYS:
            errors.append("entry has unknown key %r" % (key,))
    label = entry.get("id") if _is_non_empty_str(entry.get("id")) \
        else "entry"
    if not _is_non_empty_str(entry.get("id")):
        errors.append("%s: id is required" % label)
    for key in ("source", "revision", "format", "runtime", "abi",
                "licence"):
        if not _is_str_or_null(entry.get(key)):
            errors.append("%s: %s must be a string or null"
                          % (label, key))
    files = entry.get("files")
    if not isinstance(files, list) or not files:
        errors.append("%s: files must be a non-empty list" % label)
    else:
        for index, item in enumerate(files):
            item_label = "%s.files[%d]" % (label, index)
            if not isinstance(item, dict):
                errors.append("%s: must be an object" % item_label)
                continue
            for key in item:
                if key not in FILE_KEYS:
                    errors.append("%s: unknown key %r"
                                  % (item_label, key))
            if not _is_non_empty_str(item.get("name")):
                errors.append("%s: name is required" % item_label)
            size = item.get("bytes")
            if size is not None and (
                    not isinstance(size, int) or isinstance(size, bool)
                    or size < 0):
                errors.append("%s: bytes must be a whole number or null"
                              % item_label)
    estimates = entry.get("estimates")
    if not isinstance(estimates, list) or any(
            item not in ESTIMABLE_FIELDS for item in estimates):
        errors.append("%s: estimates must list known value fields"
                      % label)
    elif any(item in SOURCED_FIELDS for item in estimates):
        errors.append("%s: estimates lists a sourced field"
                      % label)
    provenance = entry.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("%s: provenance is required" % label)
    else:
        for key in provenance:
            if key not in PROVENANCE_KEYS:
                errors.append("%s: provenance has unknown key %r"
                              % (label, key))
        kind = provenance.get("kind")
        if kind == "sourced":
            if not _is_non_empty_str(provenance.get("url")):
                errors.append("%s: sourced provenance needs a url"
                              % label)
            if not _is_non_empty_str(provenance.get("date")):
                errors.append("%s: sourced provenance needs a date"
                              % label)
        elif kind == "synthetic":
            note = provenance.get("note")
            if note is not None and not isinstance(note, str):
                errors.append("%s: synthetic note must be a string"
                              % label)
        else:
            errors.append("%s: provenance.kind must be one of %s"
                          % (label, "/".join(PROVENANCE_KINDS)))
    for required in ENTRY_KEYS:
        if required not in entry:
            errors.append("%s: entry is missing %r" % (label, required))
    return errors


def validate_catalog(catalog: object) -> list:
    """Validate a whole catalog; returns a list of error strings."""
    if not isinstance(catalog, dict):
        return ["catalog must be an object"]
    errors = []
    for key in catalog:
        if key not in TOP_LEVEL_KEYS:
            errors.append("catalog has unknown key %r" % (key,))
    if catalog.get("schema_version") != SCHEMA_VERSION:
        errors.append('schema_version must be "%s"' % SCHEMA_VERSION)
    models = catalog.get("models")
    if not isinstance(models, list):
        errors.append("models must be a list")
        return errors
    seen = set()
    for entry in models:
        errors.extend(validate_entry(entry))
        if isinstance(entry, dict) and _is_non_empty_str(entry.get("id")):
            if entry["id"] in seen:
                errors.append("duplicate model id %r" % entry["id"])
            seen.add(entry["id"])
    return errors


def load_catalog(path) -> tuple:
    """Load and validate a catalog file. Returns (catalog, errors)."""
    import json

    try:
        with open(path, encoding="utf-8") as handle:
            catalog = json.load(handle)
    except (OSError, ValueError) as exc:
        return None, ["cannot read catalog: %s" % exc]
    errors = validate_catalog(catalog)
    if errors:
        return None, errors
    return catalog, []
