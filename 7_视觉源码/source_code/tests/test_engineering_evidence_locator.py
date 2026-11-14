"""Evidence locator arrays: repeats are legal, length mismatch is not.

A real run returned ``page_number: [1, 1]`` for one fact citing two blocks on
the same page. ``uniqueItems`` rejected it, which contradicted the field's own
description ("与 block_id 按索引对应"). The array is positional, so repeats must
be allowed and the length must match.
"""

from __future__ import annotations

from pipeline import validate
from pipeline.schema_loader import validate_instance


def _fact(page_number, block_id):
    return {"fact_id": "F001", "core": True, "page_number": page_number,
            "block_id": block_id, "quote": "样本事实。", "value": None,
            "value_text": "样本事实", "unit": None, "region": None}


def test_repeated_page_number_for_same_page_blocks_is_valid():
    payload = {"material_id": "M1", "facts": [_fact([1, 1], ["B001", "B002"])]}
    assert validate_instance("evidence", payload) == []


def test_cross_page_locator_stays_in_source_order():
    payload = {"material_id": "M1", "facts": [_fact([2, 3], ["B010", "B001"])]}
    assert validate_instance("evidence", payload) == []


def test_locator_length_mismatch_is_reported():
    payload = {"material_id": "M1", "facts": [_fact([1, 1], ["B001"])]}
    issues, _, _ = validate._check_consistency("正文 [F001]。", payload, "final.md")
    assert any(issue.code == "SCHEMA_INVALID" and "same length" in issue.message
               for issue in issues)
