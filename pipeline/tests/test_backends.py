from __future__ import annotations

from codedoc.kg_tools.backends import sniff_backend_kind


def test_sniff_backend_kind_kuzu_magic(tmp_path):
    db_file = tmp_path / "some-db"
    db_file.write_bytes(b"KUZU" + b"\x00" * 60)
    assert sniff_backend_kind(str(db_file)) == "kuzu"


def test_sniff_backend_kind_ladybug_magic(tmp_path):
    db_file = tmp_path / "some-db"
    db_file.write_bytes(b"LBUG" + b"\x00" * 60)
    assert sniff_backend_kind(str(db_file)) == "ladybug"


def test_sniff_backend_kind_directory_peeks_first_file(tmp_path):
    db_dir = tmp_path / "some-db"
    db_dir.mkdir()
    (db_dir / "catalog").write_bytes(b"LBUG" + b"\x00" * 60)
    assert sniff_backend_kind(str(db_dir)) == "ladybug"


def test_sniff_backend_kind_defaults_to_kuzu_when_unreadable(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert sniff_backend_kind(str(missing)) == "kuzu"
