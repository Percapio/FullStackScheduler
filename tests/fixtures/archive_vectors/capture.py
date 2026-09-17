"""Captures the committed ZIP vectors from CPython zipfile (Phase 32 §8.5).

Run once, on the Windows build machine, under CPython 3.12.10:

    python tests/fixtures/archive_vectors/capture.py

Re-capture is a reviewed change to the format contract, not test maintenance
(Phase 32 Assumption 7). The output pins the framer, not the interpreter.
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ZERO_CHUNK = bytes(4 << 20)
OUT = Path(__file__).with_name("vectors.json")

EM_DASH_MANIFEST = (
    "The following files are missing from this archive:\n"
    "  gone.jpg — moved or deleted before the archive was built\n"
).encode("utf-8")


class RecordingSink:
    """Non-seekable sink. Payload writes of the shared zero chunk are counted, not kept."""

    def __init__(self, keep_payload: bool):
        self.keep_payload = keep_payload
        self.non_payload = bytearray()
        self.archive = bytearray()
        self.offset = 0

    def write(self, chunk):
        chunk = bytes(chunk) if not isinstance(chunk, bytes) else chunk
        self.offset += len(chunk)
        if self.keep_payload:
            self.archive += chunk
        elif not (len(chunk) and chunk is ZERO_CHUNK_VIEW[0]):
            self.non_payload += chunk
        return len(chunk)

    def tell(self):
        return self.offset

    def flush(self):
        pass


ZERO_CHUNK_VIEW = [ZERO_CHUNK]


def member(name, size, date_time=(1980, 1, 1, 0, 0, 0), content=None):
    return {"name": name, "size": size, "date_time": list(date_time),
            "payload": {"kind": "hex", "value": content.hex()} if content is not None else {"kind": "zeros"}}


def write_archive(members, sink):
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for spec in members:
            info = zipfile.ZipInfo(filename=spec["name"])
            info.date_time = tuple(spec["date_time"])
            info.file_size = spec["size"]
            with archive.open(info, mode="w") as handle:
                if spec["payload"]["kind"] == "hex":
                    handle.write(bytes.fromhex(spec["payload"]["value"]))
                else:
                    remaining = spec["size"]
                    while remaining >= len(ZERO_CHUNK):
                        handle.write(ZERO_CHUNK)
                        remaining -= len(ZERO_CHUNK)
                    if remaining:
                        tail = bytes(remaining)
                        ZERO_CHUNK_VIEW[0] = tail
                        handle.write(tail)
                        ZERO_CHUNK_VIEW[0] = ZERO_CHUNK


GIB = 1 << 30

SMALL_CASES = {
    "one_ascii_member": [member("photo.jpg", 11, (2024, 5, 17, 13, 45, 31), b"hello world")],
    "empty_file": [member("empty.jpg", 0, (2020, 1, 1, 0, 0, 0), b"")],
    "utf8_name": [member("Fotoğraf_ü.jpg", 3, (2023, 7, 4, 9, 8, 7), b"abc")],
    "em_dash_manifest": [member("_MISSING.txt", len(EM_DASH_MANIFEST), (1980, 1, 1, 0, 0, 0), EM_DASH_MANIFEST)],
    "clamped_timestamps": [
        member("floor.jpg", 1, (1980, 1, 1, 0, 0, 0), b"f"),
        member("ceiling.jpg", 1, (2107, 12, 31, 23, 59, 59), b"c"),
    ],
    "name_255_bytes": [member("n" * 251 + ".jpg", 2, (2021, 2, 3, 4, 5, 6), b"xy")],
    "several_members": [
        member("a.jpg", 5, (2022, 1, 1, 1, 1, 1), b"aaaaa"),
        member("b.jpg", 0, (2022, 1, 1, 1, 1, 2), b""),
        member("c — d.jpg", 4, (2022, 1, 1, 1, 1, 3), b"cccc"),
        member("_TRUNCATED.txt", 33, (1980, 1, 1, 0, 0, 0), b"Listing was truncated to 2 files."),
    ],
}

ZIP64_CASES = {
    "below_zip64_local_threshold": [member("below.bin", 2_045_222_520)],
    "at_zip64_local_threshold": [member("at.bin", 2_045_222_521)],
    "size_past_limit": [member("big.bin", (1 << 31))],
    "offsets_past_limit": [member("one.bin", GIB), member("two.bin", GIB), member("three.bin", GIB)],
}

COUNT_CASES = {
    "count_65535": [member(f"{i:05d}", 0, content=b"") for i in range(65_535)],
    "count_65536": [member(f"{i:05d}", 0, content=b"") for i in range(65_536)],
}


def main():
    cases = []
    for case_id, members in SMALL_CASES.items():
        sink = RecordingSink(keep_payload=True)
        write_archive(members, sink)
        cases.append({"id": case_id, "regime": "whole", "members": members, "archive_hex": bytes(sink.archive).hex()})
    for case_id, members in ZIP64_CASES.items():
        sink = RecordingSink(keep_payload=False)
        write_archive(members, sink)
        cases.append({"id": case_id, "regime": "non_payload", "members": members,
                      "non_payload_hex": bytes(sink.non_payload).hex(), "total_length": sink.offset})
    for case_id, members in COUNT_CASES.items():
        sink = RecordingSink(keep_payload=True)
        write_archive(members, sink)
        cases.append({"id": case_id, "regime": "digest", "member_count": len(members), "name_width": 5,
                      "archive_sha256": hashlib.sha256(bytes(sink.archive)).hexdigest(), "total_length": sink.offset})
    OUT.write_text(json.dumps({"python": sys.version.split()[0], "platform": sys.platform, "cases": cases}, indent=1))
    print(f"wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
