/*
 * Direct3D 8 shader bytecode -> Direct3D 9 bytecode, at the token level.
 *
 * Copyright 2026 the Madeira contributors
 * SPDX-License-Identifier: BSD-2-Clause
 * (Same terms as the rest of third_party/d3d8to9, so the DLL keeps one licence.)
 *
 * Why this exists. Upstream d3d8to9 converts a Direct3D 8 shader by
 * disassembling it with D3DX, editing the text with regular expressions and
 * re-assembling it with D3DX. The edits target Microsoft's disassembler output
 * and work around Microsoft's Direct3D 9 shader validator. On Madeira neither
 * is there: D3DX is Wine's (vkd3d-shader's disassembly, Wine's assembler, a
 * different text format), and the Direct3D 9 underneath is DXMT's, whose DXSO
 * front end reads SM1 bytecode directly (research/dxmt/src/airconv/
 * dxso_decoder.hpp) and does not apply Microsoft's validation rules. So the
 * conversion is done on tokens, the way D3D8 and D3D9 actually differ:
 *
 *   vertex shaders  D3D8 binds inputs v# through the vertex declaration given
 *                   to CreateVertexShader; D3D9 vs_1_1 declares them in the
 *                   shader with dcl_<usage> v#. The dcl instructions are
 *                   inserted after the version token, one per declared input,
 *                   and vs.1.0 is raised to vs.1.1 (identical instruction set).
 *   pixel shaders   ps.1.0-1.4 bytecode is valid D3D9 ps_1_x bytecode as is;
 *                   ps.1.0 is raised to ps.1.1 (D3D9 has no ps_1_0).
 *
 * The rest of the token stream is copied unchanged. The walker below knows the
 * length of every SM1 instruction (SM1 tokens carry no length field), so the
 * END token is found by instruction, never by scanning for 0x0000FFFF, and a
 * shader with an opcode SM1 does not have is refused instead of guessed.
 *
 * No Windows headers: this file is compiled into d3d8.dll and, unchanged, into
 * the host test build/host-tests/check-d3d8-shader.py.
 */
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

namespace madeira_d3d8 {

enum class ShaderResult {
	Ok = 0,
	NoFunction,      // null bytecode pointer
	BadVersion,      // not vs.1.0/1.1 (vertex) or ps.1.0-1.4 (pixel)
	UnknownOpcode,   // an opcode SM1 does not define for this shader type
	Truncated,       // no END token within the limit
	BadInput,        // a declared input register outside v0..v15
};

inline const char *shader_result_name(ShaderResult r)
{
	switch (r) {
	case ShaderResult::Ok: return "ok";
	case ShaderResult::NoFunction: return "no bytecode";
	case ShaderResult::BadVersion: return "unsupported shader version";
	case ShaderResult::UnknownOpcode: return "opcode not valid in shader model 1";
	case ShaderResult::Truncated: return "no END token";
	case ShaderResult::BadInput: return "declared input register out of range";
	}
	return "?";
}

// One input of a D3D8 vertex declaration: register v<reg> carries D3D9
// usage <usage>/<usage_index> (D3DDECLUSAGE_* values).
struct VsInput {
	uint32_t reg;
	uint8_t usage;
	uint8_t usage_index;
};

constexpr uint32_t kEndToken = 0x0000FFFFu;
constexpr uint32_t kCommentOpcode = 0xFFFEu;
constexpr uint32_t kPhaseOpcode = 0xFFFDu;
constexpr uint32_t kMaxDwords = 65536;  // far beyond any SM1 shader

constexpr uint32_t vs_version(uint32_t major, uint32_t minor) { return 0xFFFE0000u | (major << 8) | minor; }
constexpr uint32_t ps_version(uint32_t major, uint32_t minor) { return 0xFFFF0000u | (major << 8) | minor; }

// Parameter tokens that follow an SM1 instruction token, or -1 when the opcode
// is not an SM1 instruction for this shader type. Values are D3DSIO_* opcodes;
// the counts are the D3D8/D3D9 SM1 operand layouts (destination + sources, DEF
// carries a destination and four literal DWORDs).
inline int sm1_param_count(uint32_t opcode, bool pixel, uint32_t minor)
{
	switch (opcode) {
	case 0: return 0;                       // nop
	case 1: return 2;                       // mov
	case 2: case 3: case 5: return 3;       // add sub mul
	case 4: return 4;                       // mad
	case 6: case 7: return 2;               // rcp rsq
	case 8: case 9: return 3;               // dp3 dp4
	case 10: case 11: case 12: case 13: return pixel ? -1 : 3; // min max slt sge
	case 14: case 15: case 16: return pixel ? -1 : 2;          // exp log lit
	case 17: return pixel ? -1 : 3;         // dst
	case 18: return 4;                      // lrp (vs: not SM1, but harmless to walk)
	case 19: return pixel ? -1 : 2;         // frc
	case 20: case 21: case 22: case 23: case 24: return pixel ? -1 : 3; // m4x4 m4x3 m3x4 m3x3 m3x2
	case 78: case 79: return pixel ? -1 : 2; // expp logp
	case 81: return 5;                      // def
	}
	if (!pixel)
		return -1;
	switch (opcode) {
	case 64: return minor >= 4 ? 2 : 1;     // texcoord / texcrd
	case 65: return 1;                      // texkill
	case 66: return minor >= 4 ? 2 : 1;     // tex / texld
	case 67: case 68: return 2;             // texbem texbeml
	case 69: case 70: return 2;             // texreg2ar texreg2gb
	case 71: case 72: return 2;             // texm3x2pad texm3x2tex
	case 73: case 74: return 2;             // texm3x3pad texm3x3tex
	case 76: return 3;                      // texm3x3spec
	case 77: return 2;                      // texm3x3vspec
	case 80: return 4;                      // cnd
	case 82: case 83: case 84: case 85: case 86: return 2; // texreg2rgb texdp3tex texm3x2depth texdp3 texm3x3
	case 87: return 1;                      // texdepth
	case 88: return 4;                      // cmp
	case 89: return 3;                      // bem
	case kPhaseOpcode: return 0;            // phase (ps.1.4)
	}
	return -1;
}

// Length in DWORDs of an SM1 shader including its END token, or 0 with *why set.
inline size_t sm1_dword_count(const uint32_t *code, size_t limit, ShaderResult *why)
{
	ShaderResult dummy;
	if (!why) why = &dummy;
	if (!code) { *why = ShaderResult::NoFunction; return 0; }
	const uint32_t version = code[0];
	const bool pixel = (version & 0xFFFF0000u) == 0xFFFF0000u;
	const bool vertex = (version & 0xFFFF0000u) == 0xFFFE0000u;
	const uint32_t major = (version >> 8) & 0xFF, minor = version & 0xFF;
	if ((!pixel && !vertex) || major != 1 || minor > (pixel ? 4u : 1u)) {
		*why = ShaderResult::BadVersion;
		return 0;
	}
	if (limit > kMaxDwords) limit = kMaxDwords;
	size_t i = 1;
	while (i < limit) {
		const uint32_t token = code[i];
		if (token == kEndToken) { *why = ShaderResult::Ok; return i + 1; }
		const uint32_t opcode = token & 0xFFFFu;
		if (opcode == kCommentOpcode) {
			i += 1 + ((token & 0x7FFF0000u) >> 16);
			continue;
		}
		const int params = sm1_param_count(opcode, pixel, minor);
		if (params < 0) { *why = ShaderResult::UnknownOpcode; return 0; }
		i += 1 + static_cast<size_t>(params);
	}
	*why = ShaderResult::Truncated;
	return 0;
}

// D3D9 dcl instruction for a vs_1_1 input: opcode, usage token, dst token.
inline void append_vs_dcl(std::vector<uint32_t> &out, const VsInput &in)
{
	out.push_back(31u);                                            // D3DSIO_DCL
	out.push_back(0x80000000u | in.usage | (uint32_t(in.usage_index) << 16));
	out.push_back(0x80000000u | (1u << 28) | 0x000F0000u | in.reg); // D3DSPR_INPUT, .xyzw
}

inline ShaderResult translate_vs(const uint32_t *function, size_t limit, const VsInput *inputs,
                                 size_t input_count, std::vector<uint32_t> &out)
{
	out.clear();
	ShaderResult why;
	const size_t n = sm1_dword_count(function, limit, &why);
	if (!n)
		return why;
	if ((function[0] & 0xFFFF0000u) != 0xFFFE0000u)
		return ShaderResult::BadVersion;
	out.reserve(n + 3 * input_count);
	out.push_back(vs_version(1, 1));
	uint32_t seen = 0;
	for (size_t k = 0; k < input_count; ++k) {
		if (inputs[k].reg > 15)
			return out.clear(), ShaderResult::BadInput;
		if (seen & (1u << inputs[k].reg))
			continue;  // one dcl per register; a D3D8 declaration names each once
		seen |= 1u << inputs[k].reg;
		append_vs_dcl(out, inputs[k]);
	}
	out.insert(out.end(), function + 1, function + n);
	return ShaderResult::Ok;
}

inline ShaderResult translate_ps(const uint32_t *function, size_t limit, std::vector<uint32_t> &out)
{
	out.clear();
	ShaderResult why;
	const size_t n = sm1_dword_count(function, limit, &why);
	if (!n)
		return why;
	if ((function[0] & 0xFFFF0000u) != 0xFFFF0000u)
		return ShaderResult::BadVersion;
	out.assign(function, function + n);
	if (out[0] == ps_version(1, 0))
		out[0] = ps_version(1, 1);
	return ShaderResult::Ok;
}

} // namespace madeira_d3d8
