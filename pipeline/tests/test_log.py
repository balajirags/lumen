from __future__ import annotations

from codedoc.log import _build_loc_breakdown_table


def test_build_loc_breakdown_table_prefers_category_metrics():
    table = _build_loc_breakdown_table(
        {
            "loc_by_category": {"jvm": 3661, "js": 439},
            "files_by_category": {"jvm": 69, "js": 8},
            "loc_by_language": {"java": 3661, "js": 439},
            "files_by_language": {"java": 69, "js": 8},
        }
    )

    assert table is not None
    values = [column._cells for column in table.columns]
    assert values[0] == ["jvm", "js"]
    assert values[1] == ["3,661", "439"]
    assert values[2] == ["69", "8"]


def test_build_loc_breakdown_table_falls_back_to_language_metrics():
    table = _build_loc_breakdown_table(
        {
            "loc_by_language": {"java": 3661, "js": 439},
            "files_by_language": {"java": 69, "js": 8},
        }
    )

    assert table is not None
    values = [column._cells for column in table.columns]
    assert values[0] == ["java", "js"]
    assert values[1] == ["3,661", "439"]
    assert values[2] == ["69", "8"]


def test_build_loc_breakdown_table_returns_none_without_breakdown_metrics():
    assert _build_loc_breakdown_table({"total_loc": 100}) is None
