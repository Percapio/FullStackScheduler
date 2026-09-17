"""Owned ZIP_STORED framer (Phase 32 §8, §9).

The declared length and the emitted bytes are both read from one field table
per record type, so they cannot disagree. Byte layout matches CPython 3.12.10
zipfile's non-seekable write path, including its conservative zip64 thresholds.
"""
import struct
import time
import zlib
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple, Union

LIMIT = (1 << 31) - 1
FILECOUNT_LIMIT = 0xFFFF
ZIP64_LOCAL_THRESHOLD = 2_045_222_521

FLAG_DATA_DESCRIPTOR = 0x0008
FLAG_UTF8 = 0x0800
EXTERNAL_ATTR = 0o600 << 16
SYSTEM_BYTE = 0
VERSION_DEFAULT = 20
VERSION_ZIP64 = 45

DOS_FLOOR = (1980, 1, 1, 0, 0, 0)
DOS_CEILING = (2107, 12, 31, 23, 59, 59)

DosDateTime = Tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class EncodedName:
    raw: bytes
    utf8: bool


def encode_name(name: str) -> EncodedName:
    try:
        return EncodedName(name.encode("ascii"), False)
    except UnicodeEncodeError:
        return EncodedName(name.encode("utf-8"), True)


def dos_timestamp(mtime_ns: int) -> DosDateTime:
    """Local-time DOS fields for an mtime, clamped to the representable range.

    An mtime the platform cannot convert clamps by sign: negative to the 1980
    floor, non-negative to the 2107 ceiling (Phase 32 §9.2).
    """
    try:
        converted = tuple(time.localtime(mtime_ns // 1_000_000_000)[:6])
    except (OverflowError, OSError, ValueError):
        return DOS_FLOOR if mtime_ns < 0 else DOS_CEILING
    if converted < DOS_FLOOR:
        return DOS_FLOOR
    if converted > DOS_CEILING:
        return DOS_CEILING
    return converted


def _dos_fields(stamp: DosDateTime) -> Tuple[int, int]:
    dos_date = (stamp[0] - 1980) << 9 | stamp[1] << 5 | stamp[2]
    dos_time = stamp[3] << 11 | stamp[4] << 5 | (stamp[5] // 2)
    return dos_time, dos_date


@dataclass(frozen=True)
class InlineContent:
    content: bytes
    crc: int


@dataclass
class PlannedMember:
    name: EncodedName
    source: object
    size: int
    dos_timestamp: DosDateTime
    local_offset: int
    zip64_local: bool
    zip64_fields: int


@dataclass(frozen=True)
class DirectoryPlacement:
    offset: int
    size: int
    zip64_end: bool


@dataclass(frozen=True)
class LocalHeader:
    member: PlannedMember


@dataclass(frozen=True)
class DataDescriptor:
    member: PlannedMember
    crc: int


@dataclass(frozen=True)
class CentralHeader:
    member: PlannedMember
    crc: int


@dataclass(frozen=True)
class Zip64EndRecord:
    count: int
    directory: DirectoryPlacement


@dataclass(frozen=True)
class Zip64EndLocator:
    directory: DirectoryPlacement


@dataclass(frozen=True)
class EndRecord:
    count: int
    directory: DirectoryPlacement


ZipRecord = Union[LocalHeader, DataDescriptor, CentralHeader, Zip64EndRecord, Zip64EndLocator, EndRecord]

Field = Tuple[str, Union[int, bytes]]


def _flags(member: PlannedMember) -> int:
    return FLAG_DATA_DESCRIPTOR | (FLAG_UTF8 if member.name.utf8 else 0)


def _zip64_directory_fields(member: PlannedMember) -> int:
    return (2 if member.size > LIMIT else 0) + (1 if member.local_offset > LIMIT else 0)


def _fields(record: ZipRecord) -> List[Field]:
    if isinstance(record, LocalHeader):
        m = record.member
        dos_time, dos_date = _dos_fields(m.dos_timestamp)
        sentinel = 0xFFFFFFFF if m.zip64_local else 0
        fields: List[Field] = [
            ("I", 0x04034B50),
            ("B", VERSION_ZIP64 if m.zip64_local else VERSION_DEFAULT), ("B", 0),
            ("H", _flags(m)),
            ("H", 0),
            ("H", dos_time), ("H", dos_date),
            ("I", 0),
            ("I", sentinel), ("I", sentinel),
            ("H", len(m.name.raw)), ("H", 20 if m.zip64_local else 0),
            ("raw", m.name.raw),
        ]
        if m.zip64_local:
            fields += [("H", 0x0001), ("H", 16), ("Q", 0), ("Q", 0)]
        return fields

    if isinstance(record, DataDescriptor):
        m = record.member
        size_format = "Q" if m.zip64_local else "I"
        return [("I", 0x08074B50), ("I", record.crc), (size_format, m.size), (size_format, m.size)]

    if isinstance(record, CentralHeader):
        m = record.member
        dos_time, dos_date = _dos_fields(m.dos_timestamp)
        k = m.zip64_fields
        version = VERSION_ZIP64 if (m.zip64_local or k > 0) else VERSION_DEFAULT
        size_value = 0xFFFFFFFF if m.size > LIMIT else m.size
        offset_value = 0xFFFFFFFF if m.local_offset > LIMIT else m.local_offset
        fields = [
            ("I", 0x02014B50),
            ("B", version), ("B", SYSTEM_BYTE),
            ("B", version), ("B", 0),
            ("H", _flags(m)), ("H", 0), ("H", dos_time), ("H", dos_date),
            ("I", record.crc),
            ("I", size_value), ("I", size_value),
            ("H", len(m.name.raw)), ("H", 4 + 8 * k if k > 0 else 0), ("H", 0),
            ("H", 0), ("H", 0),
            ("I", EXTERNAL_ATTR),
            ("I", offset_value),
            ("raw", m.name.raw),
        ]
        if k > 0:
            fields += [("H", 0x0001), ("H", 8 * k)]
            if m.size > LIMIT:
                fields += [("Q", m.size), ("Q", m.size)]
            if m.local_offset > LIMIT:
                fields += [("Q", m.local_offset)]
        return fields

    if isinstance(record, Zip64EndRecord):
        d = record.directory
        return [
            ("I", 0x06064B50), ("Q", 44), ("H", VERSION_ZIP64), ("H", VERSION_ZIP64),
            ("I", 0), ("I", 0), ("Q", record.count), ("Q", record.count),
            ("Q", d.size), ("Q", d.offset),
        ]

    if isinstance(record, Zip64EndLocator):
        d = record.directory
        return [("I", 0x07064B50), ("I", 0), ("Q", d.offset + d.size), ("I", 1)]

    if isinstance(record, EndRecord):
        d = record.directory
        count = min(record.count, 0xFFFF)
        return [
            ("I", 0x06054B50), ("H", 0), ("H", 0), ("H", count), ("H", count),
            ("I", min(d.size, 0xFFFFFFFF)), ("I", min(d.offset, 0xFFFFFFFF)), ("H", 0),
        ]

    raise TypeError(f"not a ZIP record: {type(record).__name__}")


_WIDTHS = {"B": 1, "H": 2, "I": 4, "Q": 8}


def record_length(record: ZipRecord) -> int:
    return sum(len(value) if kind == "raw" else _WIDTHS[kind] for kind, value in _fields(record))


def encode_record(record: ZipRecord) -> bytes:
    parts = []
    for kind, value in _fields(record):
        parts.append(value if kind == "raw" else struct.pack("<" + kind, value))
    return b"".join(parts)


# ---- The plan (§9) ----------------------------------------------------------

EXCLUSION_REASONS = {
    "Vanished": "moved or deleted before the archive was built",
    "Unreadable": "could not be read from the photos folder",
    "OutsideFolder": "is not a regular file inside the photos folder",
    "NotRegularFile": "is not a regular file inside the photos folder",
}

MISSING_HEADER = "The following files are missing from this archive:"


@dataclass(frozen=True)
class ExcludedEntry:
    name: str
    cause: str


@dataclass(frozen=True)
class MemberSpec:
    """One member before layout: a name, a size, a timestamp, and its source."""
    name: str
    size: int
    dos_timestamp: DosDateTime
    source: object


@dataclass
class ArchivePlan:
    members: List[PlannedMember]
    excluded: List[ExcludedEntry]
    declared_bytes: int
    directory: DirectoryPlacement
    file_member_count: int = 0


def first_free_name(stem: str, taken: Iterable[str]) -> str:
    """`_MISSING.txt`, then `_MISSING (2).txt`, ... first free.

    Each colliding candidate matches a distinct taken name, so the search ends
    within len(taken) + 1 candidates (Phase 32 §9.3).
    """
    taken_set = set(taken)
    candidate = f"{stem}.txt"
    ordinal = 2
    while candidate in taken_set:
        candidate = f"{stem} ({ordinal}).txt"
        ordinal += 1
    return candidate


def missing_manifest(excluded: Sequence[ExcludedEntry]) -> bytes:
    lines = [MISSING_HEADER] + [f"  {e.name} — {EXCLUSION_REASONS[e.cause]}" for e in excluded]
    return "".join(line + "\n" for line in lines).encode("utf-8")


def truncated_notice(max_files_per_folder: int) -> bytes:
    return f"Listing was truncated to {max_files_per_folder} files.".encode("utf-8")


def inline_member(name: str, content: bytes) -> MemberSpec:
    return MemberSpec(name, len(content), DOS_FLOOR, InlineContent(content, zlib.crc32(content)))


def lay_out(specs: Sequence[MemberSpec], excluded: Sequence[ExcludedEntry] = ()) -> ArchivePlan:
    """Assigns offsets and zip64 decisions, and totals the declared length."""
    members: List[PlannedMember] = []
    offset = 0
    for spec in specs:
        member = PlannedMember(
            name=encode_name(spec.name),
            source=spec.source,
            size=spec.size,
            dos_timestamp=spec.dos_timestamp,
            local_offset=offset,
            zip64_local=spec.size >= ZIP64_LOCAL_THRESHOLD,
            zip64_fields=0,
        )
        member.zip64_fields = _zip64_directory_fields(member)
        members.append(member)
        offset += record_length(LocalHeader(member)) + member.size + record_length(DataDescriptor(member, 0))

    directory_offset = offset
    directory_size = sum(record_length(CentralHeader(m, 0)) for m in members)
    zip64_end = len(members) > FILECOUNT_LIMIT or directory_offset > LIMIT or directory_size > LIMIT
    directory = DirectoryPlacement(directory_offset, directory_size, zip64_end)

    total = directory_offset + directory_size + record_length(EndRecord(len(members), directory))
    if zip64_end:
        total += record_length(Zip64EndRecord(len(members), directory)) + record_length(Zip64EndLocator(directory))

    return ArchivePlan(
        members=members,
        excluded=list(excluded),
        declared_bytes=total,
        directory=directory,
        file_member_count=sum(1 for m in members if not isinstance(m.source, InlineContent)),
    )


def end_records(plan: ArchivePlan) -> List[ZipRecord]:
    records: List[ZipRecord] = []
    if plan.directory.zip64_end:
        records.append(Zip64EndRecord(len(plan.members), plan.directory))
        records.append(Zip64EndLocator(plan.directory))
    records.append(EndRecord(len(plan.members), plan.directory))
    return records
