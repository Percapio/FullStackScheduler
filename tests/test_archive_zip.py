"""Phase 32 §8, §9 — the owned framer, the plan, timestamps, and trailers."""
import hashlib
import io
import json
import zipfile
import zlib
from pathlib import Path

import pytest

from backend.app.services.archive_zip import (
    DOS_CEILING, DOS_FLOOR, LIMIT, ZIP64_LOCAL_THRESHOLD,
    CentralHeader, DataDescriptor, DirectoryPlacement, EndRecord, ExcludedEntry,
    InlineContent, LocalHeader, MemberSpec, PlannedMember, Zip64EndLocator, Zip64EndRecord,
    dos_timestamp, encode_name, encode_record, end_records, first_free_name, lay_out,
    missing_manifest, record_length,
)

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "archive_vectors" / "vectors.json").read_text(encoding="utf-8"))
ZERO_CHUNK = bytes(4 << 20)


def planned(name: str, size: int, local_offset: int) -> PlannedMember:
    member = PlannedMember(
        name=encode_name(name), source=None, size=size, dos_timestamp=(2024, 1, 2, 3, 4, 5),
        local_offset=local_offset, zip64_local=size >= ZIP64_LOCAL_THRESHOLD, zip64_fields=0,
    )
    member.zip64_fields = (2 if size > LIMIT else 0) + (1 if local_offset > LIMIT else 0)
    return member


# ---- V1 ---------------------------------------------------------------------

SIZES = [0, 2_045_222_520, 2_045_222_521, LIMIT, LIMIT + 1]
OFFSETS = [0, LIMIT, LIMIT + 1]
NAMES = ["a", "é", "n" * 255, "é" * 127 + "n"]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("offset", OFFSETS)
def test_v1_member_record_length_equals_encoded_length(name, size, offset):
    member = planned(name, size, offset)
    for record in (LocalHeader(member), DataDescriptor(member, 0xDEADBEEF), CentralHeader(member, 0xDEADBEEF)):
        assert record_length(record) == len(encode_record(record))


@pytest.mark.parametrize("count", [0, 65_535, 65_536])
@pytest.mark.parametrize("offset", [0, LIMIT, LIMIT + 1, 0xFFFFFFFF + 1])
def test_v1_end_record_length_equals_encoded_length(count, offset):
    directory = DirectoryPlacement(offset=offset, size=LIMIT + 1, zip64_end=True)
    for record in (Zip64EndRecord(count, directory), Zip64EndLocator(directory), EndRecord(count, directory)):
        assert record_length(record) == len(encode_record(record))


def test_v1_names_of_255_bytes_encode_as_255_bytes():
    assert len(encode_name("n" * 255).raw) == 255
    assert len(encode_name("é" * 127 + "n").raw) == 255
    assert encode_name("é").utf8 and not encode_name("a").utf8


def test_zip64_local_threshold_is_zipfiles_float_rule():
    low, high = 0, LIMIT
    while low < high:
        mid = (low + high) // 2
        if mid * 1.05 > LIMIT:
            high = mid
        else:
            low = mid + 1
    assert low == ZIP64_LOCAL_THRESHOLD


# ---- V2 / V3 ----------------------------------------------------------------

def specs_for(case):
    if case["regime"] == "digest":
        width = case["name_width"]
        return [MemberSpec(f"{i:0{width}d}", 0, DOS_FLOOR, InlineContent(b"", 0)) for i in range(case["member_count"])]
    specs = []
    for m in case["members"]:
        if m["payload"]["kind"] == "hex":
            content = bytes.fromhex(m["payload"]["value"])
            source = InlineContent(content, zlib.crc32(content))
        else:
            source = None
        specs.append(MemberSpec(m["name"], m["size"], tuple(m["date_time"]), source))
    return specs


def zero_segments(size):
    while size >= len(ZERO_CHUNK):
        yield ZERO_CHUNK
        size -= len(ZERO_CHUNK)
    if size:
        yield bytes(size)


def frame(plan):
    """Yields (segment, is_payload) for a plan whose file payloads are zeros."""
    crcs = []
    for member in plan.members:
        yield encode_record(LocalHeader(member)), False
        if isinstance(member.source, InlineContent):
            crc = member.source.crc
            yield member.source.content, True
        else:
            crc = 0
            for block in zero_segments(member.size):
                crc = zlib.crc32(block, crc)
                yield block, True
        crcs.append(crc)
        yield encode_record(DataDescriptor(member, crc)), False
    for member, crc in zip(plan.members, crcs):
        yield encode_record(CentralHeader(member, crc)), False
    for record in end_records(plan):
        yield encode_record(record), False


class SegmentedArchive(io.RawIOBase):
    """A seekable read-only view over framed segments, zero payloads kept virtual."""

    def __init__(self, segments):
        self.parts = []
        offset = 0
        for segment, is_payload in segments:
            if not segment:
                continue
            virtual = is_payload and segment is ZERO_CHUNK or (is_payload and not any(segment[:64]) and len(segment) > 64)
            self.parts.append((offset, len(segment), None if virtual else bytes(segment)))
            offset += len(segment)
        self.length = offset
        self.position = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        self.position = {0: offset, 1: self.position + offset, 2: self.length + offset}[whence]
        return self.position

    def tell(self):
        return self.position

    def readinto(self, buffer):
        wanted = min(len(buffer), self.length - self.position)
        written = 0
        while written < wanted:
            for start, size, content in self.parts:
                if start <= self.position < start + size:
                    take = min(size - (self.position - start), wanted - written)
                    if content is None:
                        buffer[written:written + take] = bytes(take)
                    else:
                        begin = self.position - start
                        buffer[written:written + take] = content[begin:begin + take]
                    written += take
                    self.position += take
                    break
        return written


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["id"] for c in VECTORS["cases"]])
def test_v2_byte_identity_with_committed_vectors(case):
    plan = lay_out(specs_for(case))
    if case["regime"] == "whole":
        archive = b"".join(segment for segment, _ in frame(plan))
        assert archive.hex() == case["archive_hex"]
        assert plan.declared_bytes == len(archive)
    elif case["regime"] == "non_payload":
        non_payload = b"".join(segment for segment, is_payload in frame(plan) if not is_payload)
        assert non_payload.hex() == case["non_payload_hex"]
        assert plan.declared_bytes == case["total_length"]
    else:
        digest = hashlib.sha256()
        for segment, _ in frame(plan):
            digest.update(segment)
        assert digest.hexdigest() == case["archive_sha256"]
        assert plan.declared_bytes == case["total_length"]


@pytest.mark.parametrize(
    "case",
    [c for c in VECTORS["cases"] if c["regime"] != "digest"],
    ids=[c["id"] for c in VECTORS["cases"] if c["regime"] != "digest"],
)
def test_v3_structural_validity_under_installed_zipfile(case):
    plan = lay_out(specs_for(case))
    view = SegmentedArchive(frame(plan))
    assert view.length == plan.declared_bytes
    with zipfile.ZipFile(io.BufferedReader(view, buffer_size=1 << 20)) as archive:
        assert [info.filename for info in archive.infolist()] == [spec.name for spec in specs_for(case)]
        assert archive.testzip() is None


def test_v3_count_regime_opens_under_installed_zipfile():
    specs = [MemberSpec(f"{i:05d}", 0, DOS_FLOOR, InlineContent(b"", 0)) for i in range(65_536)]
    plan = lay_out(specs)
    archive = b"".join(segment for segment, _ in frame(plan))
    assert plan.directory.zip64_end
    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        assert len(opened.infolist()) == 65_536


# ---- V5 ---------------------------------------------------------------------

def ns(*date_time):
    import time
    return int(time.mktime(date_time + (0, 0, -1))) * 1_000_000_000


@pytest.mark.parametrize("mtime_ns, expected", [
    (ns(1970, 1, 2, 0, 0, 0), DOS_FLOOR),
    (ns(1979, 6, 1, 0, 0, 0), DOS_FLOOR),
    (ns(2107, 12, 31, 23, 59, 59), (2107, 12, 31, 23, 59, 59)),
    (ns(2108, 1, 2, 0, 0, 0), DOS_CEILING),
    (ns(2150, 1, 1, 0, 0, 0), DOS_CEILING),
    (-(10 ** 30), DOS_FLOOR),
    (10 ** 30, DOS_CEILING),
])
def test_v5_timestamps_always_encode(mtime_ns, expected):
    stamp = dos_timestamp(mtime_ns)
    assert stamp == expected
    member = PlannedMember(encode_name("x"), None, 0, stamp, 0, False, 0)
    encode_record(LocalHeader(member))
    encode_record(CentralHeader(member, 0))


def test_v5_mid_range_timestamp_is_local_time():
    import time
    mtime_ns = ns(2024, 5, 17, 13, 45, 31)
    assert dos_timestamp(mtime_ns) == tuple(time.localtime(mtime_ns // 1_000_000_000)[:6])


# ---- V6 and §9.3 ------------------------------------------------------------

def test_first_free_name_is_deterministic_and_bounded():
    assert first_free_name("_MISSING", ["a.jpg"]) == "_MISSING.txt"
    assert first_free_name("_MISSING", ["_MISSING.txt"]) == "_MISSING (2).txt"
    taken = ["_MISSING.txt"] + [f"_MISSING ({i}).txt" for i in range(2, 50)]
    assert first_free_name("_MISSING", taken) == "_MISSING (50).txt"


def test_v6_manifest_lines_end_in_lf():
    manifest = missing_manifest([
        ExcludedEntry("gone.jpg", "Vanished"),
        ExcludedEntry("locked.jpg", "Unreadable"),
        ExcludedEntry("link.jpg", "OutsideFolder"),
        ExcludedEntry("device", "NotRegularFile"),
    ]).decode("utf-8")
    lines = manifest.split("\n")
    assert lines[-1] == ""
    assert lines[0] == "The following files are missing from this archive:"
    assert lines[1] == "  gone.jpg — moved or deleted before the archive was built"
    assert lines[2] == "  locked.jpg — could not be read from the photos folder"
    assert lines[3] == lines[4].replace("device", "link.jpg")
    assert "\\n" not in manifest
