// ee_dmrender: render an Empire Earth DirectMusic segment to WAV with GothicKit/dmusic.
// Mimics what the engine's SEConductor/EEMusicManager do: load segment from the music folder,
// download bands, SetRepeats(100000), PlaySegment, and on intensity change set the master
// groove level and AutoTransition(current, GROOVE, MEASURE).
//
// usage: ee_dmrender <music_dir> <segment.sgt> <seconds> <out.wav> [rate] [schedule]
//   schedule: comma list of "<sec>:<intensity>" (intensity = EE SetIntensity value 0..3)
#include <dmusic.h>

#include <ctype.h>
#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>

static unsigned g_counts[7];
static FILE* g_log;

static void on_log(void* ctx, DmLogLevel lvl, char const* msg) {
	(void) ctx;
	unsigned i = lvl / 10;
	if (i < 7) g_counts[i]++;
	if (g_log) fprintf(g_log, "[%d] %s\n", lvl, msg);
	if (lvl <= DmLogLevel_WARN) fprintf(stderr, "dmusic[%s]: %s\n", lvl <= DmLogLevel_ERROR ? "ERR" : "WARN", msg);
}

// Case-insensitive lookup in the music folder, like the Windows loader's search directory.
static void* resolve(void* ctx, char const* name, size_t* len) {
	char const* dir = ctx;
	DIR* d = opendir(dir);
	if (!d) return NULL;
	struct dirent* e;
	char path[4096] = {0};
	while ((e = readdir(d))) {
		if (strcasecmp(e->d_name, name) == 0) {
			snprintf(path, sizeof path, "%s/%s", dir, e->d_name);
			break;
		}
	}
	closedir(d);
	if (!path[0]) {
		if (g_log) fprintf(g_log, "resolver: %s not in %s\n", name, dir);
		return NULL;
	}
	FILE* f = fopen(path, "rb");
	if (!f) return NULL;
	fseek(f, 0, SEEK_END);
	long n = ftell(f);
	fseek(f, 0, SEEK_SET);
	void* buf = malloc((size_t) n);
	if (fread(buf, 1, (size_t) n, f) != (size_t) n) {
		free(buf);
		buf = NULL;
	}
	fclose(f);
	*len = (size_t) n;
	return buf;
}

static void wr32(FILE* f, uint32_t v) { fwrite(&v, 4, 1, f); }
static void wr16(FILE* f, uint16_t v) { fwrite(&v, 2, 1, f); }

int main(int argc, char** argv) {
	if (argc < 5) {
		fprintf(stderr, "usage: %s <music_dir> <segment.sgt> <seconds> <out.wav> [rate] [schedule]\n", argv[0]);
		return 2;
	}
	char const* dir = argv[1];
	char const* sgt_name = argv[2];
	double seconds = atof(argv[3]);
	char const* out = argv[4];
	uint32_t rate = argc > 5 ? (uint32_t) atoi(argv[5]) : 44100;
	char const* schedule = argc > 6 ? argv[6] : "";

	char logpath[4096];
	snprintf(logpath, sizeof logpath, "%s.log", out);
	g_log = fopen(logpath, "w");
	Dm_setLogger(DmLogLevel_INFO, on_log, NULL);

	DmLoader* loader = NULL;
	if (DmLoader_create(&loader, DmLoader_DOWNLOAD) != DmResult_SUCCESS) return 1;
	DmLoader_addResolver(loader, resolve, (void*) dir);
	if (getenv("EE_GM_DIR")) DmLoader_addResolver(loader, resolve, getenv("EE_GM_DIR")); // gm.dls / gs.dls stand-in

	clock_t t0 = clock();
	DmSegment* sgt = NULL;
	DmResult rv = DmLoader_getSegment(loader, sgt_name, &sgt);
	double load_ms = 1000.0 * (double) (clock() - t0) / CLOCKS_PER_SEC;
	if (rv != DmResult_SUCCESS) {
		fprintf(stderr, "load failed: %d\n", rv);
		return 1;
	}
	printf("segment=%s name=\"%s\" length_mt=%.0f repeats=%u load_ms=%.1f\n", sgt_name, DmSegment_getName(sgt),
	       DmSegment_getLength(sgt), DmSegment_getRepeats(sgt), load_ms);
	DmSegment_setRepeats(sgt, 100000); // SEConductor: SetRepeatCount(100000)

	DmPerformance* perf = NULL;
	if (DmPerformance_create(&perf, rate) != DmResult_SUCCESS) return 1;
	DmPerformance_setMasterGroove(perf, 0); // SetIntensity(0) at conductor init
	if (getenv("EE_GAIN")) DmPerformance_setVolume(perf, (float) atof(getenv("EE_GAIN"))); // KsProperty volume as linear gain
	DmPerformance_playSegment(perf, sgt, DmTiming_INSTANT);

	// Parse the schedule.
	double at[64];
	int level[64];
	int nsched = 0;
	for (char const* p = schedule; *p && nsched < 64;) {
		double t = strtod(p, (char**) &p);
		if (*p != ':') break;
		int l = (int) strtol(p + 1, (char**) &p, 10);
		at[nsched] = t;
		level[nsched++] = l;
		if (*p == ',') p++;
	}

	FILE* f = fopen(out, "wb");
	uint32_t frames_total = (uint32_t) (seconds * rate);
	uint32_t data_bytes = frames_total * 4;
	fwrite("RIFF", 1, 4, f); wr32(f, 36 + data_bytes); fwrite("WAVEfmt ", 1, 8, f);
	wr32(f, 16); wr16(f, 1); wr16(f, 2); wr32(f, rate); wr32(f, rate * 4); wr16(f, 4); wr16(f, 16);
	fwrite("data", 1, 4, f); wr32(f, data_bytes);

	uint32_t block = rate / 10; // 100 ms
	int16_t* buf = malloc(block * 4);
	uint32_t done = 0;
	int next = 0;
	clock_t r0 = clock();
	while (done < frames_total) {
		double now = (double) done / rate;
		while (next < nsched && at[next] <= now) {
			// EEMusicManager: SetIntensity(level,false) then TransitionTo(currentScore, 0=GROOVE, 0=MEASURE)
			DmPerformance_setMasterGroove(perf, (int8_t) level[next]);
			DmPerformance_playTransition(perf, sgt, DmEmbellishment_GROOVE, DmTiming_MEASURE);
			if (g_log) fprintf(g_log, "### t=%.1fs intensity=%d\n", now, level[next]);
			next++;
		}
		uint32_t n = frames_total - done < block ? frames_total - done : block;
		DmPerformance_renderPcm(perf, buf, n * 2, DmRender_SHORT | DmRender_STEREO);
		fwrite(buf, 4, n, f);
		done += n;
	}
	double render_s = (double) (clock() - r0) / CLOCKS_PER_SEC;
	fclose(f);
	printf("rendered %.1fs in %.2fs CPU (%.0fx realtime) log: fatal=%u error=%u warn=%u info=%u\n", seconds, render_s,
	       seconds / (render_s > 0 ? render_s : 1e-9), g_counts[1], g_counts[2], g_counts[3], g_counts[4]);

	free(buf);
	DmPerformance_release(perf);
	DmSegment_release(sgt);
	DmLoader_release(loader);
	if (g_log) fclose(g_log);
	return 0;
}
