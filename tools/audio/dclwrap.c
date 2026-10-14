// Memory-to-memory wrapper around zlib contrib/blast (PKWARE DCL "implode" decompressor).
#include "blast.h"
#include <string.h>
struct src { unsigned char *p; unsigned n; int used; };
struct dst { unsigned char *p; unsigned cap, len; };
static unsigned in_fn(void *how, unsigned char **buf) {
    struct src *s = how; if (s->used) return 0; s->used = 1; *buf = s->p; return s->n;
}
static int out_fn(void *how, unsigned char *buf, unsigned len) {
    struct dst *d = how; unsigned n = d->cap - d->len < len ? d->cap - d->len : len;
    memcpy(d->p + d->len, buf, n); d->len += n; return n < len; /* stop early once the buffer is full */
}
int dcl_decompress(const unsigned char *in, unsigned inlen, unsigned char *out, unsigned cap, unsigned *outlen) {
    struct src s = {(unsigned char *)in, inlen, 0}; struct dst d = {out, cap, 0};
    int rc = blast(in_fn, &s, out_fn, &d, 0, 0);
    *outlen = d.len; return rc;
}
