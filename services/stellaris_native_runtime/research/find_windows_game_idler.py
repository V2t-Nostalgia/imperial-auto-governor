#!/usr/bin/env python3
"""Rank stripped PE functions that structurally resemble CGameIdler::Idle."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs


@dataclass(frozen=True)
class Candidate:
    score: int
    start: int
    end: int
    reasons: tuple[str, ...]
    instructions: tuple[str, ...]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--minimum-size", type=lambda value: int(value, 0), default=0x500)
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
    candidates: list[Candidate] = []
    for entry in pe.DIRECTORY_ENTRY_EXCEPTION:
        start = int(entry.struct.BeginAddress)
        end = int(entry.struct.EndAddress)
        if end - start < args.minimum_size or not (text_rva <= start < text_rva + len(text_data)):
            continue
        offset = start - text_rva
        prefix = text_data[offset : offset + min(end - start, 0x200)]
        decoded = list(md.disasm(prefix, image_base + start))
        lines = tuple(
            f"0x{instruction.address - image_base:08x}: "
            f"{instruction.mnemonic:<8} {instruction.op_str}"
            for instruction in decoded
        )
        joined = "\n".join(lines)
        reasons: list[str] = []
        score = 0
        if "+ 0x140]" in joined or "+ 0x140" in joined:
            score += 7
            reasons.append("object+0x140 access")
        if any(
            register in joined
            for register in (" dl", "dl,", " edx", "edx,", " rdx", "rdx,")
        ):
            score += 2
            reasons.append("second Win64 argument used")
        if "test" in joined and any(register in joined for register in ("dl", "bpl", "sil")):
            score += 3
            reasons.append("boolean gate")
        if "call" in joined and "qword ptr [" in joined:
            score += 2
            reasons.append("direct and virtual calls")
        if "rcx" in joined and any(
            mnemonic in joined for mnemonic in ("mov      rbx, rcx", "mov      rsi, rcx", "mov      r14, rcx")
        ):
            score += 1
            reasons.append("this pointer retained")
        if score:
            candidates.append(
                Candidate(score, start, end, tuple(reasons), lines[:80])
            )

    candidates.sort(key=lambda candidate: (-candidate.score, candidate.start))
    for candidate in candidates[: args.limit]:
        print(
            f"candidate rva=0x{candidate.start:08x}..0x{candidate.end:08x} "
            f"size=0x{candidate.end - candidate.start:x} score={candidate.score} "
            f"reasons={','.join(candidate.reasons)}"
        )
        print("\n".join(f"  {line}" for line in candidate.instructions))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
