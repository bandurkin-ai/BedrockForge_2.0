"""File names of the export for Windows (ТЗ 17.2.1, 17.2.3; ADR 0017 §5 «Имена»; `exporting/names.py`).

Every name is also made on the disk of the test: on Windows CI a name the rules let through and Windows refuses
(«CON.dxf», a path of 261 characters, «тело.» next to «тело») fails here."""

from __future__ import annotations

from pathlib import Path

import pytest

from bf_core.exporting import names

pytestmark = pytest.mark.windows


def _make(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(b"x")
    assert path.name in {p.name for p in folder.iterdir()}, name
    return path


def test_cyrillic_and_spaces_stay(tmp_path: Path) -> None:
    for name in ("Рудное тело 1", "Скважины 2025 — участок Северный", "ЦТМ рельефа (сентябрь)"):
        assert names.clean(name) == name
        _make(tmp_path, name + ".dxf")


def test_characters_windows_refuses_become_underscores(tmp_path: Path) -> None:
    assert names.clean('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert names.clean("таб\tи\x01конец\x7f") == "таб_и_конец_"
    _make(tmp_path, names.clean('Борт: "северный" / 2?') + ".str")


@pytest.mark.parametrize("name", ["CON", "con", "Aux", "PRN", "NUL", "COM1", "com9", "LPT1", "lpt9", "COM¹", "CON.txt",
                                  "nul.tar", "AUX "])  # fmt: skip
def test_reserved_device_names_get_an_underscore(tmp_path: Path, name: str) -> None:
    out = names.clean(name)
    assert out.startswith("_") and names.is_device(out[1:]) and not names.is_device(out)
    _make(tmp_path, out + ".dxf")


def test_names_that_are_not_devices_stay() -> None:
    for name in ("CONSOLE", "COM10", "LPT0", "auxiliary", "Конус"):
        assert names.clean(name) == name


def test_dots_and_spaces_at_the_end_go(tmp_path: Path) -> None:
    assert names.clean("тело.") == "тело"
    assert names.clean("  тело . . ") == "тело"
    assert names.clean("...") == names.EMPTY
    assert names.clean("") == names.EMPTY
    _make(tmp_path, names.clean("линии.") + ".str")


def test_long_names_and_paths_fit_windows(tmp_path: Path) -> None:
    long = "Очень длинное имя каркаса " * 10
    assert len(names.clean(long)) <= names.MAX_NAME
    # a deep folder: the stem is cut with a tag of the whole name, the path stays within 259 characters
    deep = tmp_path / ("п" * 40) / ("к" * 40)
    deep.mkdir(parents=True)
    room = names.MAX_PATH - len(str(deep)) - 1
    folder = names.Folder(len(str(deep)))
    a = folder.claim(long + "А", (".dtm", ".str"))
    b = folder.claim(long + "Б", (".dtm", ".str"))
    assert a != b and "~" in a and "~" in b
    for stem in (a, b):
        for suffix in (".dtm", ".str"):
            path = deep / (stem + suffix)
            assert len(str(path)) <= names.MAX_PATH and len(stem + suffix) <= min(names.MAX_NAME, room)
            _make(deep, path.name)


def test_a_folder_too_deep_is_told() -> None:
    folder = names.Folder(names.MAX_PATH - 5)
    with pytest.raises(names.ExportNameError) as err:
        folder.claim("тело", (".dxf",))
    assert err.value.code == "path_too_long" and "259" in err.value.message and err.value.hint


def test_repeats_differ_in_more_than_case(tmp_path: Path) -> None:
    folder = names.Folder(len(str(tmp_path)))
    got = [folder.claim(n, (".dxf",)) for n in ("Тело", "тело", "ТЕЛО", "Тело (2)")]
    assert got == ["Тело", "тело (2)", "ТЕЛО (3)", "Тело (2) (2)"]
    for stem in got:
        _make(tmp_path, stem + ".dxf")
    assert len(list(tmp_path.iterdir())) == 4  # none written over another (NTFS ignores case)
    # a file and the files next to it: «тело.str» of a DTM is taken by the strings of another object
    other = names.Folder()
    assert other.claim("тело", (".str",)) == "тело"
    assert other.claim("Тело", (".dtm", ".str")) == "Тело (2)"
    # a folder (a drillhole set as CSV) and a file of the same stem do not clash
    mixed = names.Folder()
    assert mixed.claim("Скважины", ("",)) == "Скважины"
    assert mixed.claim("скважины", (".xlsx",)) == "скважины"
    assert mixed.claim("СКВАЖИНЫ", ("",)) == "СКВАЖИНЫ (2)"


def test_user_folders_are_cleaned_parts(tmp_path: Path) -> None:
    assert names.folder_parts("Участок 1/Борт\\Север") == ["Участок 1", "Борт", "Север"]
    assert names.folder_parts("../../Windows/./System32") == ["Windows", "System32"]  # never out of the export
    assert names.folder_parts("CON/Сектор: 2.") == ["_CON", "Сектор_ 2"]
    assert names.folder_parts(None) == [] and names.folder_parts("") == []
    path = tmp_path.joinpath(*names.folder_parts("Участок 1/CON/Сектор: 2."))
    path.mkdir(parents=True)
    assert path.is_dir()


def test_stem_of_a_file_name() -> None:
    assert names.stem_of("контуры.str") == "контуры"
    assert names.stem_of("Восточный Цители-Сопели.STR") == "Восточный Цители-Сопели"
    assert names.stem_of("тело 1.5") == "тело 1.5"  # not an extension
    assert names.stem_of(".str") == ".str"
