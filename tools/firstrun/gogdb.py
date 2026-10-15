"""What a GOG install of Empire Earth Gold leaves beside the game: the file
hash database, the product info and, for the registry seed, the CD key
(docs/reference/firstrun.md §2, §4).

The CD key is the player's. Functions here return it only inside registry
values for <home>/registry.json; nothing here prints or logs it, and the
values never go into exceptions or progress messages.
"""
import base64
import configparser
import json
from pathlib import Path
import re
import struct
import zipfile

GAME_ID = "1207658777"
SCRIPT = "goggame-%s.script" % GAME_ID
HASHDB = "goggame-%s.hashdb" % GAME_ID
INFO = "goggame-%s.info" % GAME_ID
GOGLOG = "goglog.ini"
REG_SZ, REG_BINARY, REG_DWORD = 1, 3, 4
HKLM = "HKEY_LOCAL_MACHINE\\"

# hashdb: a zip holding one member of the same name. The member is a 12-byte
# header (header size 12, version 1, record count) and fixed 1056-byte
# records: a NUL-padded UTF-8 Windows path (1024 bytes) relative to the
# install folder, then the file's MD5 as 32 hex digits [static, GOG's
# 2026 Empire Earth Gold build: 602 records, 294 under "Empire Earth\"].
HASHDB_RECORD = 1056
HASHDB_NAME = 1024


def read_hashdb(path):
    """{windows relative path: md5 hex} from a goggame-*.hashdb, or None when
    the file is not in the format above (a newer GOG layout is not an error:
    the importer then relies on its own manifest alone)."""
    try:
        with zipfile.ZipFile(path) as z:
            members = z.infolist()
            if len(members) != 1 or members[0].file_size > 64 << 20:
                return None
            data = z.read(members[0])
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return None
    if len(data) < 12:
        return None
    header, version, count = struct.unpack_from("<III", data, 0)
    if header != 12 or version != 1 or len(data) != header + count * HASHDB_RECORD:
        return None
    out = {}
    for i in range(count):
        rec = data[header + i * HASHDB_RECORD:header + (i + 1) * HASHDB_RECORD]
        name = rec[:HASHDB_NAME].split(b"\0", 1)[0]
        md5 = rec[HASHDB_NAME:]
        if not re.fullmatch(rb"[0-9a-f]{32}", md5):
            return None
        try:
            out[name.decode("utf-8")] = md5.decode("ascii")
        except UnicodeDecodeError:
            return None
    return out


def read_info(path):
    """The product info (goggame-*.info): {"gameId", "name", "buildId", ...}, or None."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _base_game(subkey, value_name):
    """The base game's keys only: not The Art of Conquest's (Mad Doc, EEAOC)."""
    return "Mad Doc" not in subkey and value_name != "EEAOC"


def _value(value_type, data):
    if value_type == "binary":
        return {"type": REG_BINARY, "data": base64.b64decode(data).hex()}
    if value_type == "dword":
        return {"type": REG_DWORD, "data": int(data)}
    return {"type": REG_SZ, "data": str(data)}


def hklm_from_script(path):
    """{key: {name: value}} from the install script, as tools/boot/seed_user.py
    reads it (its function, so the two cannot drift)."""
    import seed_user  # tools/boot, on sys.path via firstrun.paths
    return seed_user.hklm_from_script(path)


def hklm_from_goglog(path):
    """The same HKLM values from goglog.ini, the installer's own record of the
    registry it wrote: section [<game id>], RootKey_i/SubKey_i/ValueName_i/
    ValueType_i/ValueData_i for i < Regs."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read_string(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
        section = parser[GAME_ID]
        count = int(section.get("Regs", "0"))
    except (OSError, KeyError, ValueError, configparser.Error):
        return {}
    out = {}
    for i in range(count):
        if section.get("RootKey_%d" % i) != "HKLM":
            continue
        sub = section.get("SubKey_%d" % i, "")
        name = section.get("ValueName_%d" % i, "")
        if not sub or not _base_game(sub, name):
            continue
        key = out.setdefault(HKLM + sub, {})
        if name:
            try:
                key[name] = _value(section.get("ValueType_%d" % i, ""), section.get("ValueData_%d" % i, ""))
            except ValueError:
                continue
    return out


_REG_KEY = re.compile(r"^\[(.+?)\](?:\s+\d+)?\s*$")
_REG_HEX = re.compile(r'^"([^"]*)"=hex:([0-9a-fA-F,\s\\]*)$')


def hklm_from_wine_registry(path):
    """The CD key from a Wine prefix's system.reg (HKLM; a 32-bit program's
    view is Software\\Wow6432Node in a 64-bit prefix)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out, key, pending = {}, None, None
    for raw in text.splitlines():
        line = raw.strip()
        if pending is not None:                       # a hex value continued with "\"
            name, acc = pending
            acc += line
            if acc.endswith("\\"):
                pending = (name, acc[:-1])
                continue
            pending = None
            _wine_store(out, key, name, acc)
            continue
        m = _REG_KEY.match(line)
        if m:
            k = m.group(1).replace("\\\\", "\\")
            k = re.sub(r"^Software\\Wow6432Node\\", "Software\\\\", k, flags=re.I)
            key = k if k.lower() == "software\\sierra\\cdkeys" else None
            continue
        if key is None:
            continue
        m = _REG_HEX.match(line)
        if m and m.group(1) == "EmpireEarth":
            if m.group(2).endswith("\\"):
                pending = (m.group(1), m.group(2)[:-1])
            else:
                _wine_store(out, key, m.group(1), m.group(2))
    return out


def _wine_store(out, key, name, hexlist):
    data = bytes(int(b, 16) for b in hexlist.replace(" ", "").split(",") if b)
    if data:
        out.setdefault(HKLM + "Software\\Sierra\\CDKeys", {})[name] = {"type": REG_BINARY, "data": data.hex()}


def has_cd_key(hklm):
    """True when the HKLM values hold the base game's key (never the key itself)."""
    return any(k.lower().endswith("\\sierra\\cdkeys") and "EmpireEarth" in v for k, v in hklm.items())


def find_registry_source(gold_dir, wine_prefix=None):
    """(kind, path, hklm) for the first source with the CD key: the install
    script, goglog.ini, then the Wine prefix's system.reg; (None, None, {})."""
    candidates = []
    if gold_dir is not None:
        candidates += [("script", Path(gold_dir) / SCRIPT, hklm_from_script),
                       ("goglog", Path(gold_dir) / GOGLOG, hklm_from_goglog)]
    if wine_prefix is not None:
        candidates.append(("wine-registry", Path(wine_prefix) / "system.reg", hklm_from_wine_registry))
    for kind, path, read in candidates:
        if not path.is_file():
            continue
        try:
            hklm = read(path)
        except (OSError, ValueError, KeyError):
            continue
        if has_cd_key(hklm):
            return kind, path, hklm
    return None, None, {}


def describe(hklm):
    """A loggable summary: key names and value names only."""
    return {k: sorted(v) for k, v in hklm.items()}
