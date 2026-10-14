# Find code sites that reference given IAT slots (call [iat], mov reg,[iat], jmp [iat]) in a PE.
import sys, pefile, struct, re
pe = pefile.PE(sys.argv[1], fast_load=True)
pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
base = pe.OPTIONAL_HEADER.ImageBase
pat = re.compile(sys.argv[2])
slots = {}
for d in pe.DIRECTORY_ENTRY_IMPORT:
    for i in d.imports:
        n = i.name.decode() if i.name else f'#{i.ordinal}'
        if pat.search(n): slots[i.address] = n
data = pe.get_memory_mapped_image()
hits = []
for va, n in slots.items():
    needle = struct.pack('<I', va)
    for m in re.finditer(re.escape(needle), data):
        off = m.start()
        op = data[off-2:off]
        kind = None
        if op == b'\xff\x15': kind = 'call'
        elif op == b'\xff\x25': kind = 'jmp'
        elif op[0:1] == b'\x8b': kind = 'mov'
        elif op[1:2] == b'\xa1': kind = 'mov eax'
        if kind: hits.append((base + off - 2, kind, n))
for h in sorted(hits): print(hex(h[0]), h[1], h[2])
