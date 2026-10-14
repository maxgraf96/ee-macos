/* M1 random-map replay driver: portable core (pack parsing, shims, restore, compare).
 * See rmg_replay.h and docs/reference/rmg.md section 7. */
#include "rmg_replay.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- pack parsing ----------------------------------------------------------------------- */

static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint32_t pad4(uint32_t n) { return (n + 3u) & ~3u; }

int rr_pack_load(rr_pack *pk, const char *path, char *err, size_t errlen)
{
    FILE *f;
    long len;
    const uint8_t *p, *end;
    uint32_t s, i;

    memset(pk, 0, sizeof *pk);
#define FAIL(...) do { snprintf(err, errlen, __VA_ARGS__); rr_pack_free(pk); return -1; } while (0)
    f = fopen(path, "rb");
    if (!f) FAIL("cannot open %s", path);
    fseek(f, 0, SEEK_END);
    len = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (len < (long)sizeof(rr_file_header)) { fclose(f); FAIL("%s: too short", path); }
    pk->buf = (uint8_t *)malloc((size_t)len);
    if (!pk->buf || fread(pk->buf, 1, (size_t)len, f) != (size_t)len) { fclose(f); FAIL("%s: read error", path); }
    fclose(f);
    pk->len = (size_t)len;
    pk->hdr = (const rr_file_header *)pk->buf;
    if (memcmp(pk->hdr->magic, RR_MAGIC, 8) != 0 || pk->hdr->header_size != sizeof(rr_file_header))
        FAIL("%s: not an EERRP01 pack", path);
    if (pk->hdr->nargs > 4) FAIL("%s: bad nargs", path);
    p = pk->buf + sizeof(rr_file_header);
    end = pk->buf + pk->len;
    for (s = 0; s < pk->hdr->nsections; s++) {
        uint32_t tag, size, n;
        const uint8_t *q, *qend;
        if (end - p < 8) FAIL("section %u: truncated header", s);
        tag = rd32(p); size = rd32(p + 4); p += 8;
        if ((uint32_t)(end - p) < size || size < 4) FAIL("section %u: truncated", s);
        q = p + 4; qend = p + size; n = rd32(p);
        if (tag == RR_TAG_MODS) {
            if ((uint32_t)(qend - q) < n * sizeof(rr_module_rec)) FAIL("MODS truncated");
            pk->mods = (const rr_module_rec *)q; pk->nmods = n;
        } else if (tag == RR_TAG_INRG || tag == RR_TAG_OUTR) {
            rr_region *r = (rr_region *)calloc(n ? n : 1, sizeof *r);
            if (!r) FAIL("out of memory");
            for (i = 0; i < n; i++) {
                const rr_region_rec *rec = (const rr_region_rec *)q;
                if ((uint32_t)(qend - q) < sizeof *rec || (uint32_t)(qend - q) - sizeof *rec < rec->size) { free(r); FAIL("region %u truncated", i); }
                r[i].rec = rec; r[i].bytes = q + sizeof *rec;
                q += sizeof *rec + pad4(rec->size);
            }
            if (tag == RR_TAG_INRG) { pk->in = r; pk->nin = n; } else { pk->out = r; pk->nout = n; }
        } else if (tag == RR_TAG_ALOG || tag == RR_TAG_FLOG) {
            rr_event *e = (rr_event *)calloc(n ? n : 1, sizeof *e);
            if (!e) FAIL("out of memory");
            for (i = 0; i < n; i++) {
                uint32_t extra;
                if ((uint32_t)(qend - q) < 20) { free(e); FAIL("event %u truncated", i); }
                extra = rd32(q + 16);         /* init_len / len: same position in both records */
                if ((uint32_t)(qend - q) - 20 < extra) { free(e); FAIL("event %u data truncated", i); }
                e[i].rec = q; e[i].data = q + 20;
                q += 20 + pad4(extra);
            }
            if (tag == RR_TAG_ALOG) { pk->allocs = e; pk->nallocs = n; } else { pk->files = e; pk->nfiles = n; }
        } else if (tag == RR_TAG_DBGS) {
            rr_event *e = (rr_event *)calloc(n ? n : 1, sizeof *e);
            if (!e) FAIL("out of memory");
            for (i = 0; i < n; i++) {
                uint32_t l;
                if ((uint32_t)(qend - q) < 4) { free(e); FAIL("DBGS truncated"); }
                l = rd32(q);
                e[i].rec = q; e[i].data = q + 4;
                q += 4 + pad4(l);
            }
            pk->dbgs = e; pk->ndbgs = n;
        } /* unknown sections are skipped */
        p += size;
    }
    if (!pk->mods || pk->nmods != 3) FAIL("%s: MODS must list sxlrt233, Low-Level Engine, Default", path);
#undef FAIL
    return 0;
}

void rr_pack_free(rr_pack *pk)
{
    free(pk->in); free(pk->out); free(pk->allocs); free(pk->files); free(pk->dbgs); free(pk->buf);
    memset(pk, 0, sizeof *pk);
}

/* ---- ranges ----------------------------------------------------------------------------- */

static uint32_t alloc_size(const rr_alloc_rec *r)
{
    switch (r->op) {
    case RR_OP_NEW: case RR_OP_NEWA: case RR_OP_MALLOC: return r->a;
    case RR_OP_CALLOC: return r->a * r->b;
    case RR_OP_REALLOC: return r->b;
    default: return 0;
    }
}

static int cmp_range(const void *x, const void *y)
{
    const rr_range *a = (const rr_range *)x, *b = (const rr_range *)y;
    return a->lo < b->lo ? -1 : a->lo > b->lo;
}

int rr_needed_ranges(const rr_pack *pk, rr_range **out, uint32_t *n)
{
    uint32_t cap = pk->nmods + pk->nin + pk->nout + pk->nallocs + 1, k = 0, i, m;
    rr_range *r = (rr_range *)malloc(cap * sizeof *r);
    uint32_t esp = pk->hdr->esp, slo = esp - RR_STACK_BELOW, shi = esp + RR_PAGE;
    if (!r) return -1;
#define ADD(a, b) do { uint32_t lo_ = (a) & ~(RR_PAGE - 1), hi_ = (uint32_t)(((uint64_t)(b) + RR_PAGE - 1) & ~(uint64_t)(RR_PAGE - 1)); \
        if (hi_ > lo_) { r[k].lo = lo_; r[k].hi = hi_; k++; } } while (0)
    for (i = 0; i < pk->nmods; i++) ADD(pk->mods[i].base, pk->mods[i].base + pk->mods[i].size_of_image);
    for (i = 0; i < pk->nin; i++) {
        const rr_region_rec *g = pk->in[i].rec;
        if (g->kind == RR_K_STACK) { if (g->addr < slo) slo = g->addr; if (g->addr + g->size > shi) shi = g->addr + g->size; continue; }
        if (g->flags & RR_R_RESTORE) ADD(g->addr, g->addr + g->size);
    }
    ADD(slo, shi);
    for (i = 0; i < pk->nout; i++) ADD(pk->out[i].rec->addr, pk->out[i].rec->addr + pk->out[i].rec->size);
    for (i = 0; i < pk->nallocs; i++) {
        const rr_alloc_rec *a = (const rr_alloc_rec *)pk->allocs[i].rec;
        uint32_t sz = alloc_size(a);
        if (a->ret) ADD(a->ret, a->ret + (sz ? sz : 1));
    }
#undef ADD
    qsort(r, k, sizeof *r, cmp_range);
    for (i = 0, m = 0; i < k; i++) {
        if (m && r[i].lo <= r[m - 1].hi) { if (r[i].hi > r[m - 1].hi) r[m - 1].hi = r[i].hi; }
        else r[m++] = r[i];
    }
    *out = r; *n = m;
    return 0;
}

/* ---- shims ------------------------------------------------------------------------------ */

static const rr_platform *g_pf;
static const rr_pack *g_pk;
static int g_active;
static int g_trace;                       /* opt->verbose > 2: print every allocator event */
static uint32_t g_ai, g_fi;
/* sizes of blocks known to the replay (captured heap regions, blocks allocated so far) */
static struct blk { uint32_t addr, size; } *g_blk;
static uint32_t g_nblk, g_capblk;

static void fatalf(int code, const char *fmt, ...)
{
    char msg[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    g_active = 0;
    g_pf->fatal(g_pf->user, code, msg);
}

static void logf_(const char *fmt, ...)
{
    char msg[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    g_pf->log(g_pf->user, msg);
}

static void blk_add(uint32_t addr, uint32_t size)
{
    if (g_nblk == g_capblk) {
        g_capblk = g_capblk ? 2 * g_capblk : 256;
        g_blk = (struct blk *)realloc(g_blk, g_capblk * sizeof *g_blk);
        if (!g_blk) fatalf(2, "out of memory");
    }
    g_blk[g_nblk].addr = addr; g_blk[g_nblk].size = size; g_nblk++;
}

static int blk_size(uint32_t addr, uint32_t *size)
{
    uint32_t i = g_nblk;
    while (i--) if (g_blk[i].addr == addr) { *size = g_blk[i].size; return 1; }
    return 0;
}

static const char *op_name(uint32_t op)
{
    static const char *const n[] = { "?", "new", "new[]", "malloc", "calloc", "realloc", "delete", "delete[]", "free" };
    if (op < sizeof n / sizeof n[0]) return n[op];
    if (op == RR_OP_FOPEN) return "fopen";
    if (op == RR_OP_FGETS) return "fgets";
    if (op == RR_OP_FCLOSE) return "fclose";
    return "?";
}

int rr_active(void) { return g_active; }

/* Compare masks (docs/reference/rmg.md 8.4). SetRandomMapValues copies uninitialised stack slots into
   two heap vectors; those bytes depend on what ran deeper on the stack during the call (Windows'
   msvcrt, a replay's shims, the Mac's native C runtime), not on the generator. They are not
   compared; a difference in them is counted and reported as "masked", never dropped silently.
   Matched by region label; bit k of `bytes` = byte k of each `stride`-byte element. Add an entry
   only after the same analysis (where the bytes come from, why nothing reads them). */
static const struct { const char *label; uint32_t stride, bytes; } k_masks[] = {
    { "heap<-ctx+0x164", 24, 0xfu << 16 | 1u << 23 },   /* 24-byte elements: bytes 16-19 (stale stack pointer), 23 */
    { "heap<-ctx+0x204", 8, 1u << 6 | 1u << 7 },        /* 8-byte elements: bytes 6-7 (struct padding) */
};

static const char *masked_note(uint32_t n)
{
    static char buf[48];
    if (!n) return "";
    snprintf(buf, sizeof buf, ", %u masked bytes differ", n);
    return buf;
}

static uint32_t mask_for(const char *label, uint32_t *stride)
{
    size_t i;
    for (i = 0; i < sizeof k_masks / sizeof k_masks[0]; i++)
        if (!strcmp(label, k_masks[i].label)) { *stride = k_masks[i].stride; return k_masks[i].bytes; }
    return 0;
}

/* A DLL registering an atexit handler during the call (a function-local static constructed on
   first use): the DLL's static CRT calls msvcrt's __dllonexit(func, &begin, &end), which grows the
   table with msvcrt-internal _msize/realloc that the capture cannot see. In the replay the table is
   a restored heap block, so the real __dllonexit corrupts the heap. Do what it does while the
   block has room; stop if it would have to grow (that realloc is not in the capture). */
uint32_t rr_shim_dllonexit(uint32_t func, uint32_t pbegin, uint32_t pend)
{
    uint32_t b, e, cap;
    g_pf->read(g_pf->user, pbegin, &b, 4);
    g_pf->read(g_pf->user, pend, &e, 4);
    if (g_trace) logf_("  [atexit] func %#x table %#x end %#x", func, b, e);
    if (!blk_size(b, &cap)) fatalf(2, "__dllonexit: atexit table %#x is not a captured heap block", b);
    if (e < b || e + 4 > b + cap)
        fatalf(2, "__dllonexit: atexit table %#x is full (%#x of %#x bytes); msvcrt would grow it", b, e - b, cap);
    g_pf->write(g_pf->user, e, &func, 4);
    e += 4;
    g_pf->write(g_pf->user, pend, &e, 4);
    return func;
}

uint32_t rr_shim_alloc(int op, uint32_t a, uint32_t b)
{
    const rr_alloc_rec *r;
    uint32_t size;
    if (g_ai >= g_pk->nallocs)
        fatalf(1, "allocation %u (%s %#x %#x) beyond the %u recorded", g_ai, op_name((uint32_t)op), a, b, g_pk->nallocs);
    r = (const rr_alloc_rec *)g_pk->allocs[g_ai].rec;
    if (r->op != (uint32_t)op || r->a != a || r->b != b)
        fatalf(1, "allocation %u differs: recorded %s(%#x, %#x), replay %s(%#x, %#x)", g_ai, op_name(r->op), r->a, r->b,
               op_name((uint32_t)op), a, b);
    size = alloc_size(r);
    if (g_trace) logf_("  [alloc %u] %s(%#x, %#x) -> %#x", g_ai, op_name((uint32_t)op), a, b, r->ret);
    if (r->ret) {
        if (r->init_len) {
            g_pf->write(g_pf->user, r->ret, g_pk->allocs[g_ai].data, r->init_len);
        } else if (op == RR_OP_CALLOC) {
            uint8_t z[256];
            uint32_t off;
            memset(z, 0, sizeof z);
            for (off = 0; off < size; off += sizeof z) g_pf->write(g_pf->user, r->ret + off, z, size - off < sizeof z ? size - off : (uint32_t)sizeof z);
        } else if (op == RR_OP_REALLOC && a && a != r->ret) {
            uint32_t old = 0, n, off;
            uint8_t tmp[1024];
            if (!blk_size(a, &old)) fatalf(2, "realloc of unknown block %#x without captured contents", a);
            n = old < size ? old : size;
            for (off = 0; off < n; off += sizeof tmp) {
                uint32_t c = n - off < sizeof tmp ? n - off : (uint32_t)sizeof tmp;
                g_pf->read(g_pf->user, a + off, tmp, c);
                g_pf->write(g_pf->user, r->ret + off, tmp, c);
            }
        }
        blk_add(r->ret, size);
    }
    g_ai++;
    return r->ret;
}

void rr_shim_free(int op, uint32_t p)
{
    const rr_alloc_rec *r;
    if (g_ai >= g_pk->nallocs) fatalf(1, "%s(%#x) beyond the %u recorded allocator calls", op_name((uint32_t)op), p, g_pk->nallocs);
    r = (const rr_alloc_rec *)g_pk->allocs[g_ai].rec;
    if (r->op != (uint32_t)op || r->a != p)
        fatalf(1, "allocator call %u differs: recorded %s(%#x), replay %s(%#x)", g_ai, op_name(r->op), r->a, op_name((uint32_t)op), p);
    if (g_trace) logf_("  [alloc %u] %s(%#x)", g_ai, op_name((uint32_t)op), p);
    g_ai++;
}

static const rr_file_rec *next_file(uint32_t op)
{
    const rr_file_rec *r;
    if (g_fi >= g_pk->nfiles) fatalf(1, "%s beyond the %u recorded file calls", op_name(op), g_pk->nfiles);
    r = (const rr_file_rec *)g_pk->files[g_fi].rec;
    if (r->op != op) fatalf(1, "file call %u differs: recorded %s, replay %s", g_fi, op_name(r->op), op_name(op));
    return r;
}

static void read_guest_string(uint32_t addr, char *dst, uint32_t cap)
{
    uint32_t i;
    for (i = 0; i + 1 < cap; i++) {
        g_pf->read(g_pf->user, addr + i, dst + i, 1);
        if (!dst[i]) return;
    }
    dst[i] = 0;
}

uint32_t rr_shim_fopen(uint32_t path, uint32_t mode)
{
    const rr_file_rec *r = next_file(RR_OP_FOPEN);
    const char *rp = (const char *)g_pk->files[g_fi].data, *rm = rp + strlen(rp) + 1;
    char gp[1024], gm[32];
    read_guest_string(path, gp, sizeof gp);
    read_guest_string(mode, gm, sizeof gm);
    if (strcmp(gp, rp) != 0 || strcmp(gm, rm) != 0)
        fatalf(1, "fopen %u differs: recorded (\"%s\", \"%s\"), replay (\"%s\", \"%s\")", g_fi, rp, rm, gp, gm);
    g_fi++;
    return r->ret;
}

uint32_t rr_shim_fgets(uint32_t buf, int32_t n, uint32_t fp)
{
    const rr_file_rec *r = next_file(RR_OP_FGETS);
    if (r->fp != fp || r->n != (uint32_t)n)
        fatalf(1, "fgets %u differs: recorded (fp %#x, n %u), replay (fp %#x, n %d)", g_fi, r->fp, r->n, fp, n);
    if (r->ret) {
        if (r->ret != buf) fatalf(1, "fgets %u: recorded buffer %#x, replay %#x", g_fi, r->ret, buf);
        g_pf->write(g_pf->user, buf, g_pk->files[g_fi].data, r->len);
    }
    g_fi++;
    return r->ret ? buf : 0;
}

int32_t rr_shim_fclose(uint32_t fp)
{
    const rr_file_rec *r = next_file(RR_OP_FCLOSE);
    if (r->fp != fp) fatalf(1, "fclose %u differs: recorded fp %#x, replay %#x", g_fi, r->fp, fp);
    g_fi++;
    return (int32_t)r->ret;
}

/* ---- replay ----------------------------------------------------------------------------- */

static void dump(const rr_options *opt, uint32_t seq, uint32_t idx, const char *what, const void *p, uint32_t n)
{
    char path[1024];
    FILE *f;
    if (!opt->diff_dir) return;
    snprintf(path, sizeof path, "%s/%06u-%04u-%s.bin", opt->diff_dir, seq, idx, what);
    f = fopen(path, "wb");
    if (!f) return;
    fwrite(p, 1, n, f);
    fclose(f);
}

int rr_replay(const rr_platform *pf, const rr_pack *pk, const rr_options *opt)
{
    const rr_file_header *h = pk->hdr;
    rr_range *rg = NULL;
    uint32_t nrg = 0, i, fn, eax = 0, bad = 0, badbytes = 0, total = 0, masked = 0;
    uint16_t cw_out = 0;
    int status = 0, max_report = opt->max_report > 0 ? opt->max_report : 20;
    char name[49];

    g_pf = pf; g_pk = pk; g_active = 0; g_ai = g_fi = 0; g_nblk = 0; g_trace = opt->verbose > 2;
    memcpy(name, h->export_name, 48); name[48] = 0;

    if (pf->load_modules(pf->user, pk->mods, pk->nmods)) { logf_("ERROR seq=%u: module load failed", h->seq); return 2; }
    if (rr_needed_ranges(pk, &rg, &nrg)) { logf_("ERROR: out of memory"); return 2; }
    for (i = 0; i < nrg; i++)
        if (pf->map(pf->user, rg[i].lo, rg[i].hi - rg[i].lo)) { logf_("ERROR seq=%u: cannot map %#x-%#x", h->seq, rg[i].lo, rg[i].hi); free(rg); return 2; }
    free(rg);
    for (i = 0; i < pk->nin; i++) {
        const rr_region_rec *g = pk->in[i].rec;
        if (!(g->flags & RR_R_RESTORE)) continue;
        if (pf->write(pf->user, g->addr, pk->in[i].bytes, g->size)) { logf_("ERROR: cannot restore %#x+%#x", g->addr, g->size); return 2; }
        if (g->kind == RR_K_HEAP || g->kind == RR_K_HEAP_HW) blk_add(g->addr, g->size);
    }
    for (i = 0; i < h->nargs; i++) pf->write(pf->user, h->esp + 4 + 4 * i, &h->args[i], 4);
    fn = pf->export_addr(pf->user, "Default.dll", name);
    if (!fn) { logf_("ERROR: Default.dll has no export %s", name); return 2; }
    if (opt->verbose)
        logf_("seq=%u gen=%u attempt=%u %s(%#x%s) esp=%#x cw=%#x: %u regions in, %u allocator calls, %u file calls, %u out",
              h->seq, h->gen, h->attempt, name, h->args[0], h->nargs > 1 ? ", ..." : "", h->esp, h->cw_in,
              pk->nin, pk->nallocs, pk->nfiles, pk->nout);

    g_active = 1;
    if (pf->call(pf->user, fn, h->esp, (uint16_t)h->cw_in, &eax, &cw_out)) { g_active = 0; logf_("ERROR seq=%u: call failed", h->seq); return 2; }
    g_active = 0;

    if (eax != h->eax_out) { logf_("MISMATCH seq=%u %s: EAX %#x, recorded %#x", h->seq, name, eax, h->eax_out); status = 1; }
    if ((cw_out & 0xffff) != (h->cw_out & 0xffff)) { logf_("MISMATCH seq=%u %s: CW after %#x, recorded %#x", h->seq, name, cw_out, h->cw_out); status = 1; }
    if (g_ai != pk->nallocs) { logf_("MISMATCH seq=%u %s: %u of %u allocator calls replayed", h->seq, name, g_ai, pk->nallocs); status = 1; }
    if (g_fi != pk->nfiles) { logf_("MISMATCH seq=%u %s: %u of %u file calls replayed", h->seq, name, g_fi, pk->nfiles); status = 1; }

    for (i = 0; i < pk->nout; i++) {
        const rr_region_rec *g = pk->out[i].rec;
        uint8_t *act = (uint8_t *)malloc(g->size ? g->size : 1);
        uint32_t j, first = 0, ndiff = 0, mask, stride = 1, nmasked = 0;
        char label[33];
        if (!act) { logf_("ERROR: out of memory"); return 2; }
        pf->read(pf->user, g->addr, act, g->size);
        total += g->size;
        memcpy(label, g->label, 32); label[32] = 0;
        mask = mask_for(label, &stride);
        for (j = 0; j < g->size; j++)
            if (act[j] != pk->out[i].bytes[j]) {
                if (mask && (mask >> (j % stride) & 1)) { nmasked++; continue; }
                if (!ndiff) first = j;
                ndiff++;
            }
        if (nmasked) {
            masked += nmasked;
            if (opt->verbose) logf_("  masked seq=%u %s: region %u %s %#x+%#x: %u bytes differ in masked fields",
                                    h->seq, name, i, label, g->addr, g->size, nmasked);
        }
        if (ndiff) {
            if ((int)bad < max_report) {
                uint32_t a = first & ~3u, w_exp = 0, w_act = 0;
                memcpy(&w_exp, pk->out[i].bytes + a, a + 4 <= g->size ? 4 : g->size - a);
                memcpy(&w_act, act + a, a + 4 <= g->size ? 4 : g->size - a);
                logf_("MISMATCH seq=%u %s: region %u %s %#x+%#x: %u bytes differ, first at +%#x (dword expected %08x, replay %08x)",
                      h->seq, name, i, label, g->addr, g->size, ndiff, first, w_exp, w_act);
            }
            dump(opt, h->seq, i, "expected", pk->out[i].bytes, g->size);
            dump(opt, h->seq, i, "replay", act, g->size);
            bad++; badbytes += ndiff; status = 1;
        }
        free(act);
    }
    if (status == 0)
        logf_("PASS seq=%u gen=%u attempt=%u %s: %u regions (%u bytes), %u allocator calls, %u file calls, EAX %#x%s",
              h->seq, h->gen, h->attempt, name, pk->nout, total, pk->nallocs, pk->nfiles, eax, masked_note(masked));
    else
        logf_("FAIL seq=%u gen=%u attempt=%u %s: %u of %u regions differ (%u bytes)%s", h->seq, h->gen, h->attempt, name,
              bad, pk->nout, badbytes, masked_note(masked));
    return status;
}
