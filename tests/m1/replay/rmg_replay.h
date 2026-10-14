/* M1 random-map replay driver: shared definitions.
 *
 * One replay pack (.rrp, written by rmg_pack.py from a rmg_capture.js capture) describes one
 * EERMPIM* call: the guest memory it starts from, the allocation and file-I/O sequences it made,
 * and the memory it left behind. The replay is address-exact: modules at their captured bases,
 * every region at its captured address, ESP equal to the captured ESP, and allocators that
 * return the captured addresses. Spec: docs/reference/rmg.md section 7.
 *
 * Portable C99. The core (rmg_replay_core.c) knows nothing about the host: it talks to guest
 * memory and calls guest code through an rr_platform supplied by
 *   - rmg_replay_win32.c   MSVC x86 on Windows, the real DLLs (guest address == host address);
 *   - the kit harness      on the Mac, the translated DLLs (see README.md, "Mac platform").
 *
 * Pack layout (little-endian, every record padded to 4 bytes):
 *   rr_file_header (128 bytes)
 *   nsections x { u32 tag; u32 size; u8 payload[size] }
 *     'MODS'  u32 n; n x rr_module_rec
 *     'INRG'  u32 n; n x { rr_region_rec; u8 bytes[size]; pad }     regions to restore before the call
 *     'ALOG'  u32 n; n x { rr_alloc_rec; u8 init[init_len]; pad }   allocator calls, in order
 *     'FLOG'  u32 n; n x { rr_file_rec; u8 data[len]; pad }         fopen/fgets/fclose, in order
 *     'OUTR'  u32 n; n x { rr_region_rec; u8 bytes[size]; pad }     regions expected after the call
 *     'DBGS'  u32 n; n x { u32 len; u8 text[len]; pad }             OutputDebugStringA lines (info)
 */
#ifndef RMG_REPLAY_H
#define RMG_REPLAY_H

#include <stddef.h>
#include <stdint.h>

#define RR_MAGIC "EERRP01"           /* 8 bytes including the NUL */
#define RR_TAG(a, b, c, d) ((uint32_t)(a) | ((uint32_t)(b) << 8) | ((uint32_t)(c) << 16) | ((uint32_t)(d) << 24))
#define RR_TAG_MODS RR_TAG('M', 'O', 'D', 'S')
#define RR_TAG_INRG RR_TAG('I', 'N', 'R', 'G')
#define RR_TAG_ALOG RR_TAG('A', 'L', 'O', 'G')
#define RR_TAG_FLOG RR_TAG('F', 'L', 'O', 'G')
#define RR_TAG_OUTR RR_TAG('O', 'U', 'T', 'R')
#define RR_TAG_DBGS RR_TAG('D', 'B', 'G', 'S')

/* Extra stack mapped below the captured ESP (the captured window is only the top of it). */
#define RR_STACK_BELOW 0x100000u
#define RR_PAGE 0x1000u

typedef struct rr_file_header {
    char magic[8];
    uint32_t header_size;            /* 128 */
    uint32_t seq, gen, attempt, tid;
    uint32_t esp;                    /* ESP at the export's first instruction ([esp] = return address) */
    uint32_t cw_in, cw_out;          /* x87 control word at entry / exit (low 16 bits) */
    uint32_t sw_in, tw_in;           /* status / tag word at entry (diagnostic) */
    uint32_t eax_out;
    uint32_t nargs;
    uint32_t args[4];
    uint32_t flags;                  /* RR_F_* */
    uint32_t nsections;
    char export_name[48];
} rr_file_header;

#define RR_F_ALLOC_CONTENT 1u        /* ALOG records carry the initial contents of new blocks */

typedef struct rr_module_rec {       /* 52 bytes */
    char name[32];                   /* "sxlrt233.dll", "Low-Level Engine.dll", "Default.dll" */
    uint32_t base;                   /* captured load address */
    uint32_t size_of_image;
    uint32_t timestamp;              /* PE TimeDateStamp, checked against the file */
    uint32_t data_rva, data_size;    /* .data section */
} rr_module_rec;

enum rr_region_kind {
    RR_K_CTX = 1, RR_K_STACK = 2, RR_K_MODULE_DATA = 3, RR_K_HEAP = 4, RR_K_ARG = 5, RR_K_HEAP_HW = 6,
};
#define RR_R_RESTORE 1u

typedef struct rr_region_rec {       /* 48 bytes, followed by the bytes */
    uint32_t addr, size, kind, flags;
    char label[32];                  /* e.g. "ctx", "heap<-ctx+0xb4", "Default.dll .data" */
} rr_region_rec;

enum rr_op {
    RR_OP_NEW = 1, RR_OP_NEWA = 2, RR_OP_MALLOC = 3, RR_OP_CALLOC = 4, RR_OP_REALLOC = 5,
    RR_OP_DELETE = 6, RR_OP_DELETEA = 7, RR_OP_FREE = 8,
    RR_OP_FOPEN = 16, RR_OP_FGETS = 17, RR_OP_FCLOSE = 18,
};

typedef struct rr_alloc_rec {        /* 20 bytes, followed by init bytes */
    uint32_t op;                     /* RR_OP_NEW..RR_OP_FREE */
    uint32_t a, b;                   /* new/malloc: a=size; calloc: a=count b=size; realloc: a=ptr b=size; free: a=ptr */
    uint32_t ret;                    /* returned address (allocations) */
    uint32_t init_len;               /* bytes of initial contents that follow (0 if not captured) */
} rr_alloc_rec;

typedef struct rr_file_rec {         /* 20 bytes, followed by data */
    uint32_t op;                     /* RR_OP_FOPEN / FGETS / FCLOSE */
    uint32_t fp;                     /* FILE* (opaque cookie) */
    uint32_t n;                      /* fgets: buffer size */
    uint32_t ret;                    /* fopen: FILE*, fgets: buffer or 0, fclose: result */
    uint32_t len;                    /* fopen: "path\0mode\0"; fgets: returned string incl. NUL */
} rr_file_rec;

/* ---- parsed pack ------------------------------------------------------------------------ */
typedef struct rr_region {
    const rr_region_rec *rec;
    const uint8_t *bytes;
} rr_region;
typedef struct rr_event {
    const void *rec;                 /* rr_alloc_rec* or rr_file_rec* */
    const uint8_t *data;
} rr_event;

typedef struct rr_pack {
    uint8_t *buf;                    /* whole file */
    size_t len;
    const rr_file_header *hdr;
    const rr_module_rec *mods;
    uint32_t nmods;
    rr_region *in, *out;
    uint32_t nin, nout;
    rr_event *allocs, *files, *dbgs;
    uint32_t nallocs, nfiles, ndbgs;
} rr_pack;

/* An address range the replay needs (page-aligned, merged). */
typedef struct rr_range { uint32_t lo, hi; } rr_range;

/* ---- platform ------------------------------------------------------------------------------ */
typedef struct rr_platform {
    void *user;
    /* Make [addr, addr+size) exist as readable/writable guest memory at exactly that address. */
    int (*map)(void *user, uint32_t addr, uint32_t size);
    int (*write)(void *user, uint32_t addr, const void *src, uint32_t n);
    int (*read)(void *user, uint32_t addr, void *dst, uint32_t n);
    /* Load sxlrt233.dll, Low-Level Engine.dll and Default.dll at the bases in mods (in that order),
       bind their imports (msvcrt allocators and fopen/fgets/fclose to the shims below), run the
       DllMains. The shims must be in pass-through mode while the DllMains run. */
    int (*load_modules)(void *user, const rr_module_rec *mods, uint32_t n);
    uint32_t (*export_addr)(void *user, const char *module, const char *name);
    /* Call fn with ESP == esp at its first instruction, after writing the platform's own return
       address at [esp]; x87 initialised (FNINIT) then CW = cw_in. Returns EAX and the CW after. */
    int (*call)(void *user, uint32_t fn, uint32_t esp, uint16_t cw_in, uint32_t *eax, uint16_t *cw_out);
    /* Report a divergence or error and stop; must not return. code: 1 mismatch, 2 error. */
    void (*fatal)(void *user, int code, const char *msg);
    void (*log)(void *user, const char *msg);
} rr_platform;

/* ---- core ---------------------------------------------------------------------------------- */
typedef struct rr_options {
    int verbose;
    const char *diff_dir;            /* write expected/actual bytes of mismatching regions here */
    int max_report;                  /* mismatching regions to print (default 20) */
} rr_options;

int rr_pack_load(rr_pack *pk, const char *path, char *err, size_t errlen);
void rr_pack_free(rr_pack *pk);
/* Ranges to reserve/map: module images, the stack (RR_STACK_BELOW below ESP), every region and
   every block allocated during the call. Page-aligned and merged. Caller frees *out. */
int rr_needed_ranges(const rr_pack *pk, rr_range **out, uint32_t *n);
/* Full replay of one pack. Returns 0 if every check passed, 1 on mismatch, 2 on error. */
int rr_replay(const rr_platform *pf, const rr_pack *pk, const rr_options *opt);

/* Shim state, consulted by the platform's import shims. When rr_active() is 0 the platform does
   its normal thing (DllMains, anything outside the call). */
int rr_active(void);
uint32_t rr_shim_alloc(int op, uint32_t a, uint32_t b);
void rr_shim_free(int op, uint32_t p);
uint32_t rr_shim_fopen(uint32_t path, uint32_t mode);
uint32_t rr_shim_fgets(uint32_t buf, int32_t n, uint32_t fp);
int32_t rr_shim_fclose(uint32_t fp);

#endif
