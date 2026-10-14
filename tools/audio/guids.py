import sys, pefile, struct, uuid
pe = pefile.PE(sys.argv[1], fast_load=True)
base = pe.OPTIONAL_HEADER.ImageBase
for a in sys.argv[2:]:
    va = int(a, 16)
    b = pe.get_data(va - base, 16)
    print(a, '{' + str(uuid.UUID(bytes_le=b)).upper() + '}')
