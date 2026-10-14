# Disassemble a VA range of a PE with capstone, resolving IAT slots to import names.
import sys, pefile, capstone, re
pe = pefile.PE(sys.argv[1], fast_load=True)
pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
base = pe.OPTIONAL_HEADER.ImageBase
iat = {}
for d in pe.DIRECTORY_ENTRY_IMPORT:
    for i in d.imports:
        iat[i.address] = i.name.decode() if i.name else f'{d.dll.decode()}#{i.ordinal}'
start, end = int(sys.argv[2], 16), int(sys.argv[3], 16)
code = pe.get_data(start - base, end - start)
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
for ins in md.disasm(code, start):
    s = f'{ins.address:08x}  {ins.mnemonic:6} {ins.op_str}'
    for m in re.finditer(r'0x([0-9a-f]{6,8})', ins.op_str):
        v = int(m.group(1), 16)
        if v in iat: s += f'   ; {iat[v]}'
    print(s)
