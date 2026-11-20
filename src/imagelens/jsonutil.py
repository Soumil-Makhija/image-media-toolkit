"""Strict JSON decoding and type-preserving comparison for saved evidence."""

import json


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"Non-finite JSON value: {value}")


def loads(raw):
    return json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)


def equal(left, right):
    # Python equality alone treats true as 1, including inside lists and dicts.
    def canonical(value):
        return json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=True)
    return canonical(left) == canonical(right)
