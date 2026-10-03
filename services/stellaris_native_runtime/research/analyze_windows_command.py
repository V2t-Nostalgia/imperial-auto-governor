#!/usr/bin/env python3
"""Find stripped Windows Stellaris command anchors from a native token.

The Linux executable gives us a semantic command token.  The Windows build
does not ship the corresponding PDB, but the tiny virtual token method, its
vtable slot, and constructor references remain in the PE image.  This tool
recovers those structural anchors without relying on a loaded ASLR address.
"""

from __future__ import annotations

import argparse
import bisect
import struct
from dataclasses import dataclass
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs


@dataclass(frozen=True)
class SectionView:
    name: str
    rva: int
    data: bytes

    @property
    def end_rva(self) -> int:
        return self.rva + len(self.data)


def parse_int(value: str) -> int:
    return int(value, 0)


def section_views(pe: pefile.PE) -> list[SectionView]:
    return [
        SectionView(
            section.Name.rstrip(b"\0").decode("ascii", errors="replace"),
            int(section.VirtualAddress),
            section.get_data(),
        )
        for section in pe.sections
    ]


def section_for_rva(sections: list[SectionView], rva: int) -> SectionView | None:
    for section in sections:
        if section.rva <= rva < section.end_rva:
            return section
    return None


def find_all(data: bytes, needle: bytes) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            return offsets
        offsets.append(offset)
        start = offset + 1


def function_ranges(pe: pefile.PE) -> tuple[list[int], dict[int, int]]:
    starts: list[int] = []
    ends: dict[int, int] = {}
    directory = getattr(pe, "DIRECTORY_ENTRY_EXCEPTION", ())
    for entry in directory:
        begin = int(entry.struct.BeginAddress)
        end = int(entry.struct.EndAddress)
        starts.append(begin)
        ends[begin] = end
    starts.sort()
    return starts, ends


def containing_function(
    rva: int, starts: list[int], ends: dict[int, int]
) -> tuple[int, int] | None:
    index = bisect.bisect_right(starts, rva) - 1
    if index < 0:
        return None
    start = starts[index]
    end = ends[start]
    if rva < end:
        return start, end
    return None


def disassemble(
    md: Cs,
    image_base: int,
    section: SectionView,
    start_rva: int,
    end_rva: int,
) -> list[str]:
    start = max(start_rva, section.rva)
    end = min(end_rva, section.end_rva)
    code = section.data[start - section.rva : end - section.rva]
    return [
        f"  rva=0x{instruction.address - image_base:08x}  "
        f"{instruction.mnemonic:<8} {instruction.op_str}"
        for instruction in md.disasm(code, image_base + start)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--token", type=parse_int, required=True)
    parser.add_argument("--context", type=parse_int, default=0x40)
    parser.add_argument("--vtable-token-slot", type=parse_int, default=10)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    pe = pefile.PE(str(args.executable), fast_load=False)
    pe.parse_data_directories()
    image_base = int(pe.OPTIONAL_HEADER.ImageBase)
    sections = section_views(pe)
    text = next(section for section in sections if section.name == ".text")
    starts, ends = function_ranges(pe)

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    md.skipdata = True

    token_bytes = struct.pack("<I", args.token)
    token_candidates: list[tuple[int, int]] = []
    for offset in find_all(text.data, b"\xb8" + token_bytes + b"\xc3"):
        token_candidates.append((text.rva + offset, text.rva + offset + 1))

    print(f"image_base=0x{image_base:x}")
    print(f"token=0x{args.token:04x} immediate_candidates={len(token_candidates)}")
    token_function_vas: set[int] = set()
    for token_function_rva, immediate_rva in token_candidates:
        function = containing_function(token_function_rva, starts, ends)
        if function is None:
            start_rva = max(text.rva, immediate_rva - 8)
            end_rva = min(text.end_rva, immediate_rva + 12)
        else:
            start_rva, end_rva = function
        print(
            f"\n[token-immediate] rva=0x{immediate_rva:08x} "
            f"function=0x{start_rva:08x}..0x{end_rva:08x}"
        )
        if not args.summary_only:
            for line in disassemble(md, image_base, text, start_rva, end_rva):
                print(line)
        token_function_vas.add(image_base + token_function_rva)

    pointer_locations: list[int] = []
    for token_va in token_function_vas:
        pointer = struct.pack("<Q", token_va)
        for section in sections:
            if section.name not in {".rdata", ".data"}:
                continue
            for offset in find_all(section.data, pointer):
                pointer_locations.append(section.rva + offset)

    print(f"\nfunction_pointer_candidates={len(pointer_locations)}")
    vtable_starts: list[int] = []
    for pointer_rva in pointer_locations:
        section = section_for_rva(sections, pointer_rva)
        assert section is not None
        vtable_rva = pointer_rva - args.vtable_token_slot * 8
        vtable_starts.append(vtable_rva)
        print(
            f"  token-function pointer rva=0x{pointer_rva:08x} "
            f"section={section.name} inferred_vtable=0x{vtable_rva:08x}"
        )

    targets = {image_base + rva for rva in vtable_starts}
    xrefs: list[tuple[int, int, str]] = []
    # Constructors normally load their vtable with a seven-byte
    # ``lea reg, [rip+disp32]``.  Scanning this encoding directly avoids
    # asking Capstone to retain a decoded 35+ MiB text section at once.
    for offset in range(0, len(text.data) - 7):
        rex = text.data[offset]
        opcode = text.data[offset + 1]
        modrm = text.data[offset + 2]
        if not (0x48 <= rex <= 0x4f and opcode == 0x8D and modrm & 0xC7 == 0x05):
            continue
        displacement = struct.unpack_from("<i", text.data, offset + 3)[0]
        instruction_rva = text.rva + offset
        target = image_base + instruction_rva + 7 + displacement
        if target not in targets:
            continue
        instruction = next(
            md.disasm(text.data[offset : offset + 7], image_base + instruction_rva)
        )
        xrefs.append(
            (
                instruction_rva,
                target - image_base,
                f"{instruction.mnemonic} {instruction.op_str}",
            )
        )

    print(f"\nvtable_xrefs={len(xrefs)}")
    emitted_functions: set[int] = set()
    for instruction_rva, target_rva, text_line in xrefs:
        function = containing_function(instruction_rva, starts, ends)
        print(
            f"  rva=0x{instruction_rva:08x} -> 0x{target_rva:08x}  "
            f"{text_line}"
        )
        if (
            args.summary_only
            or function is None
            or function[0] in emitted_functions
        ):
            continue
        emitted_functions.add(function[0])
        section = section_for_rva(sections, function[0])
        assert section is not None
        print(
            f"\n[vtable-xref function] 0x{function[0]:08x}..0x{function[1]:08x}"
        )
        for line in disassemble(md, image_base, section, *function):
            print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
