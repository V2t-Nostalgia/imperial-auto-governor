#!/usr/bin/env python3
"""Rank stripped PE functions resembling CGalacticObject::CalcFTLPointWith.

The Linux build supplies the semantic implementation.  This utility turns
that implementation into a structural fingerprint and ranks Windows unwind
functions without depending on absolute loaded addresses.
"""

from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from capstone.x86 import X86_OP_IMM, X86_OP_MEM


@dataclass(frozen=True)
class Candidate:
    score: int
    start: int
    end: int
    reasons: tuple[str, ...]
    instructions: tuple[str, ...]


def _format_instruction(instruction, image_base: int) -> str:
    return (
        f"0x{instruction.address - image_base:08x}: "
        f"{instruction.mnemonic:<8} {instruction.op_str}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--minimum-score", type=int, default=8)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    pe = pefile.PE(str(args.executable), fast_load=False)
    image_base = int(pe.OPTIONAL_HEADER.ImageBase)
    text = next(
        section
        for section in pe.sections
        if section.Name.rstrip(b"\0") == b".text"
    )
    text_rva = int(text.VirtualAddress)
    text_data = text.get_data()

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    candidates: list[Candidate] = []
    for entry in pe.DIRECTORY_ENTRY_EXCEPTION:
        start = int(entry.struct.BeginAddress)
        end = int(entry.struct.EndAddress)
        size = end - start
        if not (0x40 <= size <= 0x500):
            continue
        if not (text_rva <= start < text_rva + len(text_data)):
            continue
        offset = start - text_rva
        code = text_data[offset : offset + size]
        raw_markers = (
            struct.pack("<I", 0x408),
            struct.pack("<I", 0xFE8),
            struct.pack("<I", 0xB504F333),
            struct.pack("<Q", 0x29F16B11C6D1E109),
        )
        if not any(marker in code for marker in raw_markers):
            continue
        decoded = list(md.disasm(code, image_base + start))
        memory_displacements: set[int] = set()
        immediates: set[int] = set()
        virtual_call_offsets: set[int] = set()
        direct_calls = 0
        for instruction in decoded:
            for operand in instruction.operands:
                if operand.type == X86_OP_MEM:
                    memory_displacements.add(operand.mem.disp & 0xFFFFFFFFFFFFFFFF)
                elif operand.type == X86_OP_IMM:
                    immediates.add(operand.imm & 0xFFFFFFFFFFFFFFFF)
            if instruction.mnemonic == "call":
                if instruction.operands and instruction.operands[0].type == X86_OP_MEM:
                    virtual_call_offsets.add(
                        instruction.operands[0].mem.disp & 0xFFFFFFFFFFFFFFFF
                    )
                else:
                    direct_calls += 1

        score = 0
        reasons: list[str] = []
        for value, weight, label in (
            (0x408, 5, "object+0x408 coordinate"),
            (0xFE8, 5, "object+0xfe8 radius"),
        ):
            if value in memory_displacements:
                score += weight
                reasons.append(label)
        for value, weight, label in (
            (0xB504F333, 4, "fixed-point lower-bound constant"),
            (0x16A09E666, 4, "fixed-point upper-bound constant"),
            (0x19A28, 3, "FTL distance constant"),
            (0x186A0, 2, "fixed-point modulus"),
            (0x29F16B11C6D1E109, 4, "signed-division multiplier"),
            (0x0A7C5AC471B47843, 3, "unsigned-division multiplier"),
        ):
            if value in immediates:
                score += weight
                reasons.append(label)
        if 0x58 in virtual_call_offsets:
            score += 5
            reasons.append("galactic-object coordinate virtual call")
        if direct_calls >= 2:
            score += 2
            reasons.append("coordinate helper calls")
        if len(decoded) and decoded[-1].mnemonic == "ret":
            score += 1
            reasons.append("complete unwind function")
        if score < args.minimum_score:
            continue
        candidates.append(
            Candidate(
                score=score,
                start=start,
                end=end,
                reasons=tuple(reasons),
                instructions=tuple(
                    _format_instruction(instruction, image_base)
                    for instruction in decoded
                ),
            )
        )

    candidates.sort(key=lambda candidate: (-candidate.score, candidate.start))
    for candidate in candidates[: args.limit]:
        print(
            f"candidate rva=0x{candidate.start:08x}..0x{candidate.end:08x} "
            f"size=0x{candidate.end - candidate.start:x} score={candidate.score} "
            f"reasons={','.join(candidate.reasons)}"
        )
        if not args.summary_only:
            print("\n".join(f"  {line}" for line in candidate.instructions))
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
