"""US-101 downloader without network: pinned hashes, streaming, fail-closed checks, resume, cache.

Review findings covered: ASOS-3 (no trust on first use), ASOS-4 (no path from outside is joined
unchecked), ASOS-12 (streaming, size check, no re-download, --verify-only without a manifest).
The file contents here are made-up test bytes (nature: synthetic); only test_pins_match_adr10
reads the real pinned values, from ADR-10 itself.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import pytest

from pipeline.ingest import asos_download as dl

ROOT = Path(__file__).resolve().parents[1]
CONTENT = {
    "event_table_training.p": b"events train " * 50,
    "event_table_testing.p": b"events test " * 40,
    "customer_nodes_training.p": b"customers train " * 70,
}


def pin(data: bytes, osf_id: str = "abcde") -> dl.Pinned:
    return dl.Pinned(hashlib.sha256(data).hexdigest(), len(data), osf_id)


FAKE = {name: pin(data, f"id{i}") for i, (name, data) in enumerate(CONTENT.items())}
URLS = {dl.download_url(p.osf_id): name for name, p in FAKE.items()}


class Server:
    """A fake OSF: serves CONTENT (or overrides) by download URL and counts requests."""

    def __init__(self, overrides: dict[str, bytes] | None = None, fail_on: str | None = None):
        self.overrides = overrides or {}
        self.fail_on = fail_on
        self.requests: list[str] = []

    def __call__(self, url: str) -> BinaryIO:
        name = URLS[url]
        self.requests.append(name)
        if name == self.fail_on:
            raise ConnectionResetError(f"connection lost while fetching {name}")
        return io.BytesIO(self.overrides.get(name, CONTENT[name]))


@pytest.fixture
def fake_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the module-level pins at the small fake files (main() and convert use them)."""
    monkeypatch.setattr(dl, "EXPECTED", FAKE)


def leftovers(dest: Path) -> list[str]:
    return sorted(p.name for p in dest.iterdir() if p.name.endswith(".part"))


# ---- Pins --------------------------------------------------------------------------------------


def test_pins_match_adr10() -> None:
    """EXPECTED holds exactly the six files of ADR-10, with the same bytes and sha256."""
    adr = (ROOT / "docs/adr/ADR-10-asos-two-protocols.md").read_text()
    rows = re.findall(r"^\| (\w+\.p) \| ([\d,]+) \| ([0-9a-f]{64}) \|$", adr, flags=re.M)
    assert len(rows) == 6
    from_adr = {name: (sha, int(size.replace(",", ""))) for name, size, sha in rows}
    assert {name: (p.sha256, p.size) for name, p in dl.EXPECTED.items()} == from_adr
    assert all(re.fullmatch(r"[a-z0-9]{5}", p.osf_id) for p in dl.EXPECTED.values())
    assert len({p.osf_id for p in dl.EXPECTED.values()}) == 6
    assert dl.download_url("d9tmv") == "https://osf.io/download/d9tmv/"


def test_pins_cannot_be_modified_at_runtime() -> None:
    with pytest.raises(TypeError):
        dl.EXPECTED["extra.p"] = dl.Pinned("0" * 64, 1, "xxxxx")  # type: ignore[index]


# ---- Download ----------------------------------------------------------------------------------


def test_download_streams_checks_and_writes_a_log(tmp_path: Path) -> None:
    server = Server()
    actions = dl.download(tmp_path, opener=server, expected=FAKE)
    assert actions == dict.fromkeys(CONTENT, "downloaded")
    for name, data in CONTENT.items():
        assert (tmp_path / name).read_bytes() == data
    assert leftovers(tmp_path) == []
    log = json.loads((tmp_path / dl.MANIFEST).read_text())
    assert "CC BY 4.0" in log["attribution"]
    assert log["files"]["event_table_training.p"] == {
        "sha256": FAKE["event_table_training.p"].sha256,
        "size": FAKE["event_table_training.p"].size,
        "osf_id": "id0",
        "action": "downloaded",
    }
    assert dl.verify(tmp_path, FAKE) == {}


def test_download_reads_in_chunks_not_whole_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl, "CHUNK", 64)
    reads: list[int] = []

    class Recording(io.BytesIO):
        def read(self, n: int | None = -1, /) -> bytes:
            reads.append(-1 if n is None else n)
            return super().read(n)

    def opener(url: str) -> BinaryIO:
        return Recording(CONTENT[URLS[url]])

    dl.download(tmp_path, opener=opener, expected=FAKE, names=["event_table_training.p"])
    assert reads and all(n == 64 for n in reads)


def test_tampered_content_is_rejected_and_never_installed(tmp_path: Path) -> None:
    name = "event_table_testing.p"
    good = CONTENT[name]
    tampered = good[:-1] + b"!"  # same size, different bytes
    server = Server(overrides={name: tampered})
    with pytest.raises(dl.IntegrityError, match="sha256"):
        dl.download(tmp_path, opener=server, expected=FAKE)
    assert not (tmp_path / name).exists()
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize("served", [b"short", b"x" * 10_000])
def test_wrong_size_is_rejected(tmp_path: Path, served: bytes) -> None:
    server = Server(overrides={"event_table_training.p": served})
    with pytest.raises(dl.IntegrityError, match="bytes"):
        dl.download(tmp_path, opener=server, expected=FAKE, names=["event_table_training.p"])
    assert not (tmp_path / "event_table_training.p").exists()
    assert leftovers(tmp_path) == []


def test_oversized_stream_is_cut_off_early(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl, "CHUNK", 100)
    consumed: list[int] = []

    class Endless(io.RawIOBase):
        def read(self, n: int = -1, /) -> bytes:
            consumed.append(n)
            return b"x" * n

    with pytest.raises(dl.IntegrityError, match="more than"):
        dl.download(
            tmp_path,
            opener=lambda url: Endless(),  # type: ignore[arg-type,return-value]
            expected=FAKE,
            names=["event_table_training.p"],
        )
    assert sum(consumed) <= FAKE["event_table_training.p"].size + 100


@pytest.mark.parametrize("name", ["evil.p", "../event_table_training.p", "MANIFEST.json", ""])
def test_unknown_file_names_are_rejected_before_any_request(tmp_path: Path, name: str) -> None:
    server = Server()
    with pytest.raises(ValueError, match="unknown ASOS file"):
        dl.download(tmp_path, opener=server, expected=FAKE, names=["event_table_training.p", name])
    assert server.requests == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name", ["../escaped.p", "sub/escaped.p", "..\\escaped.p", "/tmp/escaped.p", "..", "."]
)
def test_names_with_path_parts_are_rejected_even_when_pinned(tmp_path: Path, name: str) -> None:
    """Defence in depth (ASOS-4): a bad name is refused even if it were in the pinned table."""
    data = b"payload"
    dest = tmp_path / "data" / "raw" / "asos"
    with pytest.raises(ValueError, match="not a plain file name"):
        dl.download(dest, opener=lambda url: io.BytesIO(data), expected={name: pin(data)})
    assert not (tmp_path / "data" / "raw" / "escaped.p").exists()
    assert not (tmp_path / "escaped.p").exists()


def test_symlink_escaping_dest_is_rejected(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    outside = tmp_path / "outside.p"
    (dest / "event_table_training.p").symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        dl.target_path(dest, "event_table_training.p", FAKE)


def test_download_is_resumable(tmp_path: Path) -> None:
    first = Server(fail_on="event_table_testing.p")
    with pytest.raises(ConnectionResetError):
        dl.download(tmp_path, opener=first, expected=FAKE)
    assert first.requests == ["event_table_training.p", "event_table_testing.p"]
    assert (tmp_path / "event_table_training.p").exists()
    assert not (tmp_path / "event_table_testing.p").exists()
    assert leftovers(tmp_path) == []

    second = Server()
    actions = dl.download(tmp_path, opener=second, expected=FAKE)
    assert second.requests == ["event_table_testing.p", "customer_nodes_training.p"]
    assert actions == {
        "event_table_training.p": "present",
        "event_table_testing.p": "downloaded",
        "customer_nodes_training.p": "downloaded",
    }

    third = Server()
    assert set(dl.download(tmp_path, opener=third, expected=FAKE).values()) == {"present"}
    assert third.requests == []


def test_a_present_but_corrupt_file_is_downloaded_again(tmp_path: Path) -> None:
    dl.download(tmp_path, opener=Server(), expected=FAKE)
    (tmp_path / "event_table_testing.p").write_bytes(b"corrupt")
    server = Server()
    actions = dl.download(tmp_path, opener=server, expected=FAKE)
    assert server.requests == ["event_table_testing.p"]
    assert actions["event_table_testing.p"] == "downloaded"
    assert dl.verify(tmp_path, FAKE) == {}


# ---- Local cache (--source-dir) -----------------------------------------------------------------


def make_cache(path: Path, content: dict[str, bytes]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name, data in content.items():
        (path / name).write_bytes(data)
    (path / "unrelated.p").write_bytes(b"ignored")
    return path


def test_source_dir_is_linked_after_the_hash_check(tmp_path: Path) -> None:
    cache = make_cache(tmp_path / "cache", CONTENT)
    dest = tmp_path / "dest"
    server = Server()
    actions = dl.download(dest, source_dir=cache, opener=server, expected=FAKE)
    assert server.requests == []
    assert set(actions.values()) == {"linked"}
    for name in CONTENT:
        assert os.path.samefile(dest / name, cache / name)
    assert not (dest / "unrelated.p").exists()
    assert dl.verify(dest, FAKE) == {}


def test_source_dir_is_copied_when_links_are_not_possible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_link(src: object, dst: object) -> None:
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(dl.os, "link", no_link)
    cache = make_cache(tmp_path / "cache", CONTENT)
    dest = tmp_path / "dest"
    actions = dl.download(dest, source_dir=cache, opener=Server(), expected=FAKE)
    assert set(actions.values()) == {"copied"}
    assert not os.path.samefile(dest / "event_table_training.p", cache / "event_table_training.p")
    assert dl.verify(dest, FAKE) == {}


@pytest.mark.parametrize("link", [True, False])
def test_a_tampered_cache_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link: bool
) -> None:
    if not link:
        monkeypatch.setattr(dl.os, "link", lambda src, dst: (_ for _ in ()).throw(OSError()))
    content = dict(CONTENT)
    content["event_table_testing.p"] = content["event_table_testing.p"][:-1] + b"?"
    cache = make_cache(tmp_path / "cache", content)
    dest = tmp_path / "dest"
    server = Server()
    with pytest.raises(dl.IntegrityError, match="event_table_testing.p"):
        dl.download(dest, source_dir=cache, opener=server, expected=FAKE)
    assert not (dest / "event_table_testing.p").exists()
    assert server.requests == []
    assert leftovers(dest) == []


def test_files_missing_from_the_cache_are_downloaded(tmp_path: Path) -> None:
    cache = make_cache(
        tmp_path / "cache", {"event_table_training.p": CONTENT["event_table_training.p"]}
    )
    server = Server()
    actions = dl.download(tmp_path / "dest", source_dir=cache, opener=server, expected=FAKE)
    assert actions["event_table_training.p"] == "linked"
    assert server.requests == ["event_table_testing.p", "customer_nodes_training.p"]


# ---- Verification -------------------------------------------------------------------------------


def test_verify_uses_the_pins_not_the_log(tmp_path: Path) -> None:
    """ASOS-3: rewriting the log to match tampered bytes does not make them pass."""
    dl.download(tmp_path, opener=Server(), expected=FAKE)
    tampered = b"tampered!" * 10
    (tmp_path / "event_table_training.p").write_bytes(tampered)
    log = json.loads((tmp_path / dl.MANIFEST).read_text())
    log["files"]["event_table_training.p"]["sha256"] = hashlib.sha256(tampered).hexdigest()
    log["files"]["event_table_training.p"]["size"] = len(tampered)
    (tmp_path / dl.MANIFEST).write_text(json.dumps(log))
    problems = dl.verify(tmp_path, FAKE)
    assert list(problems) == ["event_table_training.p"]
    assert "size" in problems["event_table_training.p"]


def test_verify_reports_missing_wrong_size_and_wrong_hash(tmp_path: Path) -> None:
    dl.download(tmp_path, opener=Server(), expected=FAKE)
    (tmp_path / "event_table_training.p").unlink()
    (tmp_path / "event_table_testing.p").write_bytes(b"x")
    data = CONTENT["customer_nodes_training.p"]
    (tmp_path / "customer_nodes_training.p").write_bytes(data[:-1] + b"?")
    problems = dl.verify(tmp_path, FAKE)
    assert problems["event_table_training.p"] == "missing"
    assert problems["event_table_testing.p"].startswith("size 1 bytes")
    assert problems["customer_nodes_training.p"].startswith("sha256 ")


def test_verify_of_a_missing_directory_reports_every_file(tmp_path: Path) -> None:
    assert dl.verify(tmp_path / "nowhere", FAKE) == dict.fromkeys(CONTENT, "missing")


# ---- Command line --------------------------------------------------------------------------------


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = dl.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def test_verify_only_without_files_or_manifest_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ASOS-12: no FileNotFoundError, a non-zero exit code and the six missing names."""
    code, _, err = run(["--dest", str(tmp_path / "empty"), "--verify-only"], capsys)
    assert code == 1
    for name in dl.EXPECTED:
        assert f"{name}: missing" in err


def test_verify_only_passes_without_a_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fake_pins: None
) -> None:
    dl.download(tmp_path, opener=Server(), expected=FAKE)
    (tmp_path / dl.MANIFEST).unlink()
    code, out, _ = run(["--dest", str(tmp_path), "--verify-only"], capsys)
    assert code == 0
    assert "match the pinned sha256" in out


def test_main_from_a_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fake_pins: None
) -> None:
    cache = make_cache(tmp_path / "cache", CONTENT)
    dest = tmp_path / "dest"
    code, out, _ = run(["--dest", str(dest), "--source-dir", str(cache)], capsys)
    assert code == 0
    assert "event_table_training.p: linked" in out


def test_main_reports_a_failed_download(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    fake_pins: None,
) -> None:
    def offline(url: str) -> BinaryIO:
        raise OSError("network is unreachable")

    monkeypatch.setattr(dl, "open_url", offline)
    code, _, err = run(["--dest", str(tmp_path)], capsys)
    assert code == 1
    assert "network is unreachable" in err


def test_main_reports_tampered_content(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    fake_pins: None,
) -> None:
    opener: Callable[[str], BinaryIO] = Server(overrides={"event_table_training.p": b"?" * 650})
    monkeypatch.setattr(dl, "open_url", opener)
    code, _, err = run(["--dest", str(tmp_path)], capsys)
    assert code == 1
    assert "sha256" in err


def test_open_url_fetches_only_osf_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, float]] = []

    def fake_urlopen(url: str, timeout: float) -> BinaryIO:
        calls.append((url, timeout))
        return io.BytesIO(b"data")

    monkeypatch.setattr(dl.urllib.request, "urlopen", fake_urlopen)
    with dl.open_url(dl.download_url("d9tmv")) as stream:
        assert stream.read() == b"data"
    assert calls == [("https://osf.io/download/d9tmv/", dl.TIMEOUT_S)]
    for url in (
        "http://osf.io/download/d9tmv/",
        "https://example.com/download/d9tmv/",
        "file:///x",
    ):
        with pytest.raises(ValueError, match="not an OSF download URL"):
            dl.open_url(url)
    assert len(calls) == 1


@pytest.mark.filterwarnings("ignore:'pipeline.ingest.asos_download' found in sys.modules")
def test_module_entry_point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy
    import sys

    argv = ["asos_download", "--dest", str(tmp_path), "--verify-only"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("pipeline.ingest.asos_download", run_name="__main__")
    assert stop.value.code == 1  # nothing downloaded: six files missing


def test_verify_only_with_an_escaping_symlink_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "event_table_training.p").symlink_to(tmp_path / "outside.p")
    code, _, err = run(["--dest", str(dest), "--verify-only"], capsys)
    assert code == 1
    assert "resolves outside" in err


def test_i2_a_planted_symlink_at_the_log_part_file_is_not_followed(tmp_path: Path) -> None:
    """The log is written through a temporary name in dest: a symlink planted there must not
    redirect the write to a file outside dest (review I2)."""
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / f".{dl.MANIFEST}.part").symlink_to(outside)
    dl.download(dest, opener=Server(), expected=FAKE)
    assert outside.read_text() == "keep me"
    assert not (dest / f".{dl.MANIFEST}.part").exists()
    assert not (dest / f".{dl.MANIFEST}.part").is_symlink()
    log = json.loads((dest / dl.MANIFEST).read_text())
    assert set(log["files"]) == set(CONTENT)
