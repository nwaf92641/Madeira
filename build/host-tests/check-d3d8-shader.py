#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 the Madeira contributors
"""Direct3D 8 shaders on the Direct3D 8 -> 9 path, checked against DXMT's own reader.

d3d8.dll (third_party/d3d8to9, built by build/d3d8/build.sh) translates D3D8
shader bytecode on tokens (third_party/d3d8to9/source/madeira_d3d8_shader.hpp)
and hands the result to DXMT's d3d9, whose CreateVertexShader/CreatePixelShader
accept a blob only if parse_dxso_header and walk_dxso_shader do
(research/dxmt/src/d3d9/d3d9_device.cpp). This test compiles the production
translator and DXMT's production DXSO header/decoder natively and checks, on
hand-encoded D3D8 shaders:

  * vs.1.1 / vs.1.0: one dcl per declared input is inserted (usage, index and
    register as the declaration says), vs.1.0 becomes vs.1.1, every other token
    is unchanged, and DXMT's walker accepts the result and reads the same dcls;
  * ps.1.1 (with a negated constant, which D3D8 allows) / ps.1.4 (texld, texcrd,
    phase) / ps.1.0: passed through (ps.1.0 -> ps.1.1) and accepted by DXMT;
  * the translator's SM1 instruction lengths agree with DXMT's on every case,
    including a def whose literal is 0x0000FFFF (an END look-alike) and a
    comment block;
  * refusals: vs_2_0, an opcode SM1 lacks, a missing END, a declared input
    past v15; each with its reason.

Needs only a C++17 compiler. Nothing here runs a GPU or a Wine session.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRANSLATOR = ROOT / 'third_party/d3d8to9/source'
AIRCONV = ROOT / 'research/dxmt/src/airconv'
CXX = os.environ.get('CXX') or shutil.which('c++') or shutil.which('g++') or shutil.which('clang++')

TEST = r'''
#include "madeira_d3d8_shader.hpp"
#include "dxso_header.hpp"
#include "dxso_decoder.hpp"
#include <cstdio>
#include <cstring>
#include <vector>

using namespace madeira_d3d8;
static int failed = 0;
static void check(bool ok, const char *what) {
  std::printf("%s: %s\n", ok ? "PASS" : "FAIL", what);
  if (!ok) failed++;
}

// DXMT's own length of a blob: offset of End + 1, as shader_bytecode_dword_count.
static size_t dxmt_length(const std::vector<uint32_t> &code) {
  auto header = dxmt::parse_dxso_header(code.data(), code.size());
  if (!header) return 0;
  dxmt::DxsoBytecodeIter it(code.data(), (uint32_t)code.size(), *header);
  dxmt::DxsoInstruction ins{};
  while (it.next(ins))
    if (ins.opcode == dxmt::DxsoOpcode::End) return ins.offset_dwords + 1;
  return 0;
}
static bool dxmt_accepts(const std::vector<uint32_t> &code, dxmt::DxsoShaderKind kind,
                         dxmt::DxsoShaderMetadata *out = nullptr) {
  auto header = dxmt::parse_dxso_header(code.data(), code.size());
  if (!header || header->kind != kind) return false;
  auto md = dxmt::walk_dxso_shader(code.data(), (uint32_t)code.size(), *header);
  if (md && out) *out = *md;
  return md.has_value();
}

static uint32_t f2u(float f) { uint32_t u; std::memcpy(&u, &f, 4); return u; }

int main() {
  // ---- vertex shader, vs.1.1 -------------------------------------------
  const std::vector<uint32_t> vs11 = {
    0xFFFE0101,
    9, 0xC0010000, 0x90E40000, 0xA0E40000,          // dp4 oPos.x, v0, c0
    9, 0xC0020000, 0x90E40000, 0xA0E40001,          // dp4 oPos.y, v0, c1
    9, 0xC0040000, 0x90E40000, 0xA0E40002,          // dp4 oPos.z, v0, c2
    9, 0xC0080000, 0x90E40000, 0xA0E40003,          // dp4 oPos.w, v0, c3
    1, 0xD00F0000, 0x90E40005,                      // mov oD0, v5
    1, 0xE0030000, 0x90E40007,                      // mov oT0.xy, v7
    1, 0xC0010001, 0xA0FF0004,                      // mov oFog.x, c4.w
    1, 0xB0010000, 0xA0E40005,                      // mov a0.x, c5
    1, 0x800F0000, 0xA0E42006,                      // mov r0, c[a0.x + 6]
    0x0002FFFE, 0x64636261, 0x68676665,             // comment, 2 dwords
    81, 0xA00F000A, f2u(1.f), 0x0000FFFF, 0, f2u(1.f), // def c10 (a literal that looks like END)
    0x0000FFFF,
  };
  const VsInput inputs[] = { {0, 0, 0}, {5, 10, 0}, {7, 5, 0} }; // position, color, texcoord
  ShaderResult why;
  check(sm1_dword_count(vs11.data(), 1000, &why) == vs11.size() && why == ShaderResult::Ok,
        "the SM1 walker finds END by instruction, past a def literal of 0x0000FFFF");
  check(dxmt_length(vs11) == vs11.size(), "DXMT's walker agrees on the vs.1.1 length");
  std::vector<uint32_t> out;
  check(translate_vs(vs11.data(), 1000, inputs, 3, out) == ShaderResult::Ok, "vs.1.1 translates");
  check(out.size() == vs11.size() + 9 && out[0] == 0xFFFE0101, "one dcl (3 tokens) per declared input, version vs_1_1");
  check(std::equal(vs11.begin() + 1, vs11.end(), out.begin() + 10), "every D3D8 token after the version is unchanged");
  check(out[1] == 31 && out[2] == 0x80000000 && out[3] == 0x900F0000, "dcl_position v0");
  check(out[4] == 31 && out[5] == 0x8000000A && out[6] == 0x900F0005, "dcl_color v5");
  check(out[7] == 31 && out[8] == 0x80000005 && out[9] == 0x900F0007, "dcl_texcoord v7");
  dxmt::DxsoShaderMetadata md;
  check(dxmt_accepts(out, dxmt::DxsoShaderKind::Vertex, &md), "DXMT accepts the translated vs_1_1 (CreateVertexShader)");
  check(md.dcls.size() == 3 && md.dcls[1].dcl.usage == dxmt::DxsoUsage::Color && md.dcls[1].bound_to.num == 5
        && md.dcls[2].dcl.usage == dxmt::DxsoUsage::Texcoord && md.dcls[2].bound_to.type == dxmt::DxsoRegisterType::Input,
        "DXMT reads the inserted dcls as the declaration bound them");
  check(dxmt_length(out) == out.size(), "DXMT's walker agrees on the translated length");

  // A texcoord with an index, and a repeated register (declared once).
  const VsInput indexed[] = { {0, 0, 0}, {3, 5, 2}, {3, 5, 2} };
  check(translate_vs(vs11.data(), 1000, indexed, 3, out) == ShaderResult::Ok
        && out.size() == vs11.size() + 6 && out[5] == 0x80020005, "a usage index is encoded; a register is declared once");

  // ---- vs.1.0 -------------------------------------------------------------
  std::vector<uint32_t> vs10 = { 0xFFFE0100, 1, 0xC00F0000, 0x90E40000, 0x0000FFFF };
  check(translate_vs(vs10.data(), 100, inputs, 1, out) == ShaderResult::Ok && out[0] == 0xFFFE0101,
        "vs.1.0 is raised to vs.1.1");
  check(dxmt_accepts(out, dxmt::DxsoShaderKind::Vertex), "DXMT accepts the raised vs.1.0");

  // ---- pixel shaders ----------------------------------------------------
  const std::vector<uint32_t> ps11 = {
    0xFFFF0101,
    66, 0xB00F0000,                                 // tex t0
    5, 0x800F0000, 0xB0E40000, 0x90E40000,          // mul r0, t0, v0
    2, 0x800F0000, 0x80E40000, 0xA1E40000,          // add r0, r0, -c0 (D3D8 allows it)
    0x40000001, 0x80080000, 0xA0FF0001,             // +mov r0.a, c1.a (co-issued)
    0x0000FFFF,
  };
  check(dxmt_length(ps11) == ps11.size() && sm1_dword_count(ps11.data(), 100, nullptr) == ps11.size(),
        "ps.1.1 lengths agree (co-issue bit ignored for the opcode)");
  check(translate_ps(ps11.data(), 100, out) == ShaderResult::Ok && out == ps11, "ps.1.1 passes unchanged");
  check(dxmt_accepts(out, dxmt::DxsoShaderKind::Pixel), "DXMT accepts ps.1.1 with a negated constant");

  const std::vector<uint32_t> ps14 = {
    0xFFFF0104,
    81, 0xA00F0000, f2u(.5f), f2u(.5f), f2u(.5f), f2u(1.f), // def c0
    64, 0x800F0001, 0xB0E40001,                     // texcrd r1, t1
    66, 0x800F0000, 0xB0E40000,                     // texld r0, t0
    0x0000FFFD,                                     // phase
    66, 0x800F0002, 0x80E40001,                     // texld r2, r1
    4, 0x800F0000, 0x80E40000, 0x80E40002, 0xA0E40000, // mad r0, r0, r2, c0
    0x0000FFFF,
  };
  check(dxmt_length(ps14) == ps14.size() && sm1_dword_count(ps14.data(), 100, nullptr) == ps14.size(),
        "ps.1.4 lengths agree (texld/texcrd take a source, phase none)");
  check(translate_ps(ps14.data(), 100, out) == ShaderResult::Ok && out == ps14, "ps.1.4 passes unchanged");
  check(dxmt_accepts(out, dxmt::DxsoShaderKind::Pixel), "DXMT accepts ps.1.4");

  const std::vector<uint32_t> ps13 = {
    0xFFFF0103,
    66, 0xB00F0000,                                 // tex t0
    73, 0xB00F0001, 0xB0E40000,                     // texm3x3pad t1, t0
    73, 0xB00F0002, 0xB0E40000,                     // texm3x3pad t2, t0
    76, 0xB00F0003, 0xB0E40000, 0xA0E40000,         // texm3x3spec t3, t0, c0
    80, 0x800F0000, 0x80E40000, 0xB0E40003, 0x90E40000, // cnd r0, r0.a, t3, v0 (simplified)
    0x0000FFFF,
  };
  check(dxmt_length(ps13) == ps13.size() && sm1_dword_count(ps13.data(), 100, nullptr) == ps13.size(),
        "ps.1.3 texm3x3spec / cnd lengths agree");
  check(translate_ps(ps13.data(), 100, out) == ShaderResult::Ok && dxmt_accepts(out, dxmt::DxsoShaderKind::Pixel),
        "DXMT accepts ps.1.3");

  std::vector<uint32_t> ps10 = { 0xFFFF0100, 66, 0xB00F0000, 1, 0x800F0000, 0xB0E40000, 0x0000FFFF };
  check(translate_ps(ps10.data(), 100, out) == ShaderResult::Ok && out[0] == 0xFFFF0101
        && dxmt_accepts(out, dxmt::DxsoShaderKind::Pixel), "ps.1.0 is raised to ps.1.1 and accepted");

  // ---- refusals --------------------------------------------------------
  std::vector<uint32_t> vs20 = { 0xFFFE0200, 1, 0xC00F0000, 0x90E40000, 0x0000FFFF };
  check(translate_vs(vs20.data(), 100, inputs, 1, out) == ShaderResult::BadVersion && out.empty(),
        "vs_2_0 is not a D3D8 shader: refused");
  check(translate_ps(vs11.data(), 1000, out) == ShaderResult::BadVersion, "a vertex shader given as a pixel shader is refused");
  std::vector<uint32_t> psmin = { 0xFFFF0101, 10, 0x800F0000, 0x80E40000, 0x80E40001, 0x0000FFFF };
  check(translate_ps(psmin.data(), 100, out) == ShaderResult::UnknownOpcode, "an opcode ps.1.x lacks (min) is refused");
  std::vector<uint32_t> vspow = { 0xFFFE0101, 32, 0x800F0000, 0x80E40000, 0x80E40001, 0x0000FFFF };
  check(translate_vs(vspow.data(), 100, inputs, 1, out) == ShaderResult::UnknownOpcode, "an SM2 opcode (pow) in vs.1.1 is refused");
  std::vector<uint32_t> noend = { 0xFFFE0101, 1, 0xC00F0000, 0x90E40000 };
  check(translate_vs(noend.data(), noend.size(), inputs, 1, out) == ShaderResult::Truncated, "no END within the blob is refused");
  const VsInput bad[] = { {16, 0, 0} };
  check(translate_vs(vs10.data(), 100, bad, 1, out) == ShaderResult::BadInput && out.empty(), "an input past v15 is refused");
  check(translate_vs(nullptr, 100, inputs, 1, out) == ShaderResult::NoFunction, "no bytecode is refused");
  check(std::strcmp(shader_result_name(ShaderResult::UnknownOpcode), "opcode not valid in shader model 1") == 0,
        "each refusal has a reason for the launch log");

  std::printf("%d failure(s)\n", failed);
  return failed ? 1 : 0;
}
'''


def main() -> int:
    if not CXX:
        print('FAIL: no C++ compiler (set CXX)')
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / 'd3d9.h').write_text('/* dxso_header.hpp includes d3d9.h for nothing it uses here */\n')
        (work / 'test.cpp').write_text(TEST)
        binary = work / 'test'
        build = subprocess.run([CXX, '-std=c++17', '-O1', '-Wall', '-fsanitize=address,undefined',
                                '-I', str(work), '-I', str(TRANSLATOR), '-I', str(AIRCONV),
                                str(work / 'test.cpp'), '-o', str(binary)], capture_output=True, text=True)
        if build.returncode != 0:
            print(build.stdout + build.stderr)
            print('FAIL: the translator and DXMT\'s DXSO reader compile together')
            return 1
        run = subprocess.run([str(binary)], capture_output=True, text=True)
        print(run.stdout, end='')
        if run.stderr:
            print(run.stderr, file=sys.stderr, end='')
        if run.returncode == 0:
            print('PASS: d3d8 shader translation (DXMT accepts every translated shader)')
        return run.returncode


if __name__ == '__main__':
    raise SystemExit(main())
