import sys, pefile, re
pe = pefile.PE(sys.argv[1], fast_load=True)
pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_EXPORT'], pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
pat = re.compile(sys.argv[2]) if len(sys.argv) > 2 else None
mode = sys.argv[3] if len(sys.argv) > 3 else 'exp'
if mode == 'exp' and hasattr(pe, 'DIRECTORY_ENTRY_EXPORT'):
    for e in pe.DIRECTORY_ENTRY_EXPORT.symbols:
        n = (e.name or b'').decode()
        if pat is None or pat.search(n):
            print(hex(pe.OPTIONAL_HEADER.ImageBase + e.address), e.ordinal, n)
if mode == 'imp':
    for d in pe.DIRECTORY_ENTRY_IMPORT:
        for i in d.imports:
            n = (i.name or b'').decode() if i.name else f'#{i.ordinal}'
            if pat is None or pat.search(d.dll.decode()+':'+n):
                print(d.dll.decode(), hex(i.address), n)
