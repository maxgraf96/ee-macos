/* Self-test of the replay core on any host: a mock platform (sparse guest memory, no x86) and a
 * C stand-in for an EERMPIM export that uses every shim. Pairs with make_capture.py; run.sh
 * builds both and expects PASS for the faithful capture and MISMATCH for the broken one. */
#include "rmg_replay.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct page { uint32_t base; uint8_t b[RR_PAGE]; } page;
static page *g_pages;
static uint32_t g_npages, g_cap;

static page *find(uint32_t a)
{
    uint32_t i, base = a & ~(RR_PAGE - 1);
    for (i = 0; i < g_npages; i++) if (g_pages[i].base == base) return &g_pages[i];
    return NULL;
}
static int m_map(void *u, uint32_t addr, uint32_t size)
{
    uint32_t a;
    (void)u;
    for (a = addr & ~(RR_PAGE - 1); a < addr + size; a += RR_PAGE) {
        if (find(a)) continue;
        if (g_npages == g_cap) {
            g_cap = g_cap ? 2 * g_cap : 64;
            g_pages = (page *)realloc(g_pages, g_cap * sizeof *g_pages);
        }
        g_pages[g_npages].base = a;
        memset(g_pages[g_npages].b, 0, RR_PAGE);
        g_npages++;
    }
    return 0;
}
static int m_write(void *u, uint32_t addr, const void *src, uint32_t n)
{
    uint32_t i;
    (void)u;
    for (i = 0; i < n; i++) {
        page *p = find(addr + i);
        if (!p) { fprintf(stderr, "write to unmapped %#x\n", addr + i); exit(2); }
        p->b[(addr + i) & (RR_PAGE - 1)] = ((const uint8_t *)src)[i];
    }
    return 0;
}
static int m_read(void *u, uint32_t addr, void *dst, uint32_t n)
{
    uint32_t i;
    (void)u;
    for (i = 0; i < n; i++) {
        page *p = find(addr + i);
        if (!p) { fprintf(stderr, "read from unmapped %#x\n", addr + i); exit(2); }
        ((uint8_t *)dst)[i] = p->b[(addr + i) & (RR_PAGE - 1)];
    }
    return 0;
}
static uint32_t rd(uint32_t a) { uint32_t v; m_read(NULL, a, &v, 4); return v; }
static void wr(uint32_t a, uint32_t v) { m_write(NULL, a, &v, 4); }

static int m_load(void *u, const rr_module_rec *mods, uint32_t n)
{
    uint32_t i;
    (void)u;
    for (i = 0; i < n; i++) printf("  module %s at %#x\n", mods[i].name, mods[i].base);
    return 0;
}
static uint32_t m_export(void *u, const char *module, const char *name)
{
    (void)u;
    return !strcmp(module, "Default.dll") && !strcmp(name, "EERMPIMCreateFlatMap") ? 1 : 0;
}

/* The stand-in "game code": see make_capture.py. */
static uint32_t fake_export(uint32_t esp)
{
    uint32_t ctx = rd(esp + 4), counter = rd(ctx + 0x10), old = rd(ctx + 0x20), blk, fp, buf = esp - 0x400, len = 0;
    char c;
    m_write(NULL, esp - 0x200, "C:\\x\\Test.rmv", 14);
    m_write(NULL, esp - 0x100, "r", 2);
    blk = rr_shim_alloc(RR_OP_NEW, 16, 0);
    fp = rr_shim_fopen(esp - 0x200, esp - 0x100);
    if (rr_shim_fgets(buf, 512, fp))
        for (len = 0; m_read(NULL, buf + len, &c, 1), c; len++) { }
    while (rr_shim_fgets(buf, 512, fp)) { }
    rr_shim_fclose(fp);
    wr(blk, len); wr(blk + 4, counter + 1); wr(blk + 8, 0x12345678); wr(blk + 12, 0);
    wr(ctx + 0x10, counter + 1);
    wr(ctx + 0x20, blk);
    rr_shim_free(RR_OP_DELETE, old);
    return 0;
}
static int m_call(void *u, uint32_t fn, uint32_t esp, uint16_t cw_in, uint32_t *eax, uint16_t *cw_out)
{
    (void)u;
    if (fn != 1) return -1;
    wr(esp, 0xdeadbeef);   /* the platform's return address */
    *eax = fake_export(esp);
    *cw_out = cw_in;
    return 0;
}
static void m_fatal(void *u, int code, const char *msg) { (void)u; printf("%s %s\n", code == 1 ? "MISMATCH" : "ERROR", msg); exit(code); }
static void m_log(void *u, const char *msg) { (void)u; printf("%s\n", msg); }

int main(int argc, char **argv)
{
    rr_pack pk;
    rr_options opt;
    rr_platform pf = { NULL, m_map, m_write, m_read, m_load, m_export, m_call, m_fatal, m_log };
    char err[256];
    if (argc != 2) { fprintf(stderr, "usage: selftest PACK.rrp\n"); return 2; }
    if (rr_pack_load(&pk, argv[1], err, sizeof err)) { printf("ERROR %s\n", err); return 2; }
    memset(&opt, 0, sizeof opt);
    opt.verbose = 1;
    return rr_replay(&pf, &pk, &opt);
}
