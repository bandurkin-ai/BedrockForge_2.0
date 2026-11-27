"""A dialog in a `.bfggis` of another person is not trusted (подшаг 23.21б-B): the meta and the parts are checked, the
sum of the sizes is counted, anything wrong is 409 `assistant_dialog_broken`, never a 500 or a flood of the memory."""

from __future__ import annotations

import copy
from typing import Any

import pytest
import sqlalchemy as sa
import zstandard

from bf_core.api.errors import ApiError
from bf_core.assistant import store_parts as sp
from bf_core.assistant.store import Dialogs
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.storage.blobs import BlobStore
from tests import test_assistant_store_project as fx
from tests.test_assistant_store import ANSWER, ask

# fixtures of the project tests (an assignment: the parameters below must not 'redefine' an import)
proj = fx.proj
dlg = fx.dlg
temp = fx.temp

HEX = "ab" * 32


def _forge(proj: Any, ident: str, change: Any) -> None:
    with dialect.write_tx(proj.engine) as conn:
        meta = copy.deepcopy(conn.execute(sa.select(objects.c.meta).where(objects.c.id == int(ident))).scalar_one())
        change(meta)
        conn.execute(sa.update(objects).where(objects.c.id == int(ident)).values(meta=meta))


def _made(dlg: Dialogs) -> str:
    head = ask(dlg, "Вопрос")
    dlg.append(head["id"], ANSWER, [])
    return str(head["id"])


def _broken(call: Any, *args: Any) -> None:
    with pytest.raises(ApiError) as e:
        call(*args)
    assert (e.value.status, e.value.code) == (409, "assistant_dialog_broken")
    assert e.value.message and e.value.hint


def _drop(key: str) -> Any:
    return lambda m: m.pop(key)


FORGERIES = {
    "version": lambda m: m.update(v=2),
    "parts_missing": _drop("parts"),
    "parts_not_a_list": lambda m: m.update(parts="abc"),
    "a_name_that_is_not_a_hash": lambda m: m.update(parts=["../../outside"]),
    "a_short_hash": lambda m: m.update(parts=["ab12"]),
    "too_many_parts": lambda m: m.update(parts=[HEX] * 401),
    "bytes_over_32_mib": lambda m: m.update(bytes=(32 << 20) + 1),
    "bytes_not_a_number": lambda m: m.update(bytes="many"),
    "last_at_missing": _drop("last_at"),
    "uid_missing": _drop("uid"),
    "pending_without_tools_list": lambda m: m.update(pending={"id": "p", "tools": "x"}),
    "pending_not_an_object": lambda m: m.update(pending=[1]),
    "state_unknown": lambda m: m.update(state="exploded"),
    "meta_not_an_object": lambda m: m.clear() or m.update(x=1),
}


@pytest.mark.parametrize("name", sorted(FORGERIES))
def test_a_forged_meta_is_409_broken_and_the_dialog_can_be_deleted(proj: Any, dlg: Dialogs, name: str) -> None:
    ident = _made(dlg)
    _forge(proj, ident, FORGERIES[name])
    _broken(dlg.read, ident)
    _broken(dlg.append, ident, ANSWER, [])
    _broken(dlg.copy_to, ident, Dialogs(None))
    listed = dlg.list()  # the list is not broken by it: the dialog is shown in the state `error`
    assert listed["total"] == 1 and listed["items"][0]["state"] == "error"
    dlg.delete(ident)
    assert dlg.list()["total"] == 0


def test_the_sum_of_the_parts_must_not_exceed_the_bytes_of_the_meta(proj: Any, dlg: Dialogs) -> None:
    ident = _made(dlg)
    shas = dlg.read(ident).meta["parts"]
    total = sum(sp.raw_size(BlobStore(proj.folder).read_bytes(s)) for s in shas)
    _forge(proj, ident, lambda m: m.update(bytes=total - 1))
    _broken(dlg.read, ident)
    _broken(dlg.copy_to, ident, Dialogs(None))
    _forge(proj, ident, lambda m: m.update(bytes=total))
    assert dlg.read(ident).messages  # the right sum: read as before


def test_a_part_file_larger_than_its_share_is_not_even_read(
    proj: Any,
    dlg: Dialogs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ident = _made(dlg)
    store = BlobStore(proj.folder)
    for sha in dlg.read(ident).meta["parts"]:
        with store.path_of(sha).open("ab") as fh:
            fh.write(b"\0" * 4096)  # padding after the frame
    reads: list[str] = []
    real = BlobStore.read_bytes
    monkeypatch.setattr(BlobStore, "read_bytes", lambda self, s: reads.append(s) or real(self, s))
    _broken(dlg.read, ident)
    assert reads == []


def test_a_decompression_bomb_is_cut_at_the_limit(proj: Any, dlg: Dialogs) -> None:
    bomb = zstandard.ZstdCompressor().compress(b'{"messages": [], "pad": "' + b"a" * (40 << 20) + b'"}')
    assert len(bomb) < 1 << 20
    with pytest.raises(ApiError) as e:
        sp.decode_sized(bomb, 1 << 20)
    assert e.value.code == "assistant_dialog_broken"
    with pytest.raises(ApiError):
        sp.decode_sized(bomb)  # above 32 MiB, too
    _broken(sp.decode_sized, b"\x00" * 10, 0)


def test_a_valid_dialog_is_unchanged_by_the_check(proj: Any, dlg: Dialogs) -> None:
    ident = _made(dlg)
    loaded = dlg.read(ident)
    assert sp.check_meta(loaded.meta) == loaded.meta
    assert loaded.messages
