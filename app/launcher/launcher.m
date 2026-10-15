// launcher.m - EmpireEarth.app's main executable: the first run, then the game.
//
// The shareable app (tools/package_app.py, docs/reference/packaging.md) holds
// no game code. On every start this program asks the first-run driver
// (tools/firstrun/firstrun.py, run by the app's own Python) for the state of
// the player's data folder:
//
//   ready          exec() the game host (Contents/MacOS/EmpireEarthHost, the
//                  kit's player host) with the bundles, symbols.json, the
//                  imported game and the player's profile in its environment;
//   anything else  show the first-run window: choose the GOG installer or game
//                  folder (or drop it on the window, or pick what `probe`
//                  found), then import and build with one progress bar, and
//                  start the game when that is done.
//
// Nothing is written inside the app (App Translocation makes it read-only):
// everything goes to the data folder, ~/Library/Application Support/
// EmpireEarth, or $EE_HOME for tests.
//
//   EmpireEarth                         the app: window when needed
//   EmpireEarth --import PATH           the window, importing PATH at once
//   EmpireEarth --no-ui [--import PATH] [--no-play] [--jobs N]
//                                       no window: status, import (PATH, or
//                                       the first usable thing probe finds),
//                                       build, then the game unless --no-play;
//                                       firstrun's JSON lines on stdout
//   EmpireEarth --status                print firstrun's status result, exit
//
// $EE_FIRSTRUN_NICE lowers the build's priority (tests); $EE_PROBE_PATHS
// (colon-separated) replaces probe's usual places.
//
// docs/reference/firstrun.md §6 is the contract with firstrun.py.
#import <AppKit/AppKit.h>

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

extern char **environ;

static NSString *const kHostName = @"EmpireEarthHost";

// The firstrun.py child running now: a SIGINT or SIGTERM to the launcher is
// passed on to it, so it stops its compilers and leaves a state the next
// start resumes (exit 130), instead of building on without anyone waiting.
static volatile pid_t g_child_pid = 0;

static void forward_stop(int sig) {
    pid_t child = g_child_pid;
    if (child > 0)
        kill(child, SIGTERM);
    (void)sig;
}

// ---------------------------------------------------------------- paths --

@interface EEPaths : NSObject
@property(copy) NSString *resources, *python, *firstrun, *kit, *cc, *lld, *innoextract, *host;
@property(copy) NSString *home, *logs;
@end

@implementation EEPaths
+ (instancetype)current {
    EEPaths *p = [EEPaths new];
    NSBundle *bundle = NSBundle.mainBundle;
    p.resources = bundle.resourcePath;
    NSString *tc = [p.resources stringByAppendingPathComponent:@"toolchain"];
    NSString *ee = [p.resources stringByAppendingPathComponent:@"ee"];
    p.python = [tc stringByAppendingPathComponent:@"python/bin/python3"];
    p.cc = [tc stringByAppendingPathComponent:@"llvm/bin/clang"];
    p.lld = [tc stringByAppendingPathComponent:@"llvm/bin/ld64.lld"];
    p.firstrun = [ee stringByAppendingPathComponent:@"tools/firstrun/firstrun.py"];
    p.kit = [ee stringByAppendingPathComponent:@"kit"];
    p.innoextract = [ee stringByAppendingPathComponent:@"bin/innoextract"];
    p.host = [bundle.executablePath.stringByDeletingLastPathComponent
        stringByAppendingPathComponent:kHostName];
    NSString *home = NSProcessInfo.processInfo.environment[@"EE_HOME"];
    if (home.length)
        p.home = home.stringByExpandingTildeInPath.stringByStandardizingPath;
    else
        p.home = [NSHomeDirectory() stringByAppendingPathComponent:
                                        @"Library/Application Support/EmpireEarth"];
    p.logs = [p.home stringByAppendingPathComponent:@"Logs"];
    return p;
}

// What is missing from the app itself (a damaged or partial copy), or nil.
- (NSString *)missing {
    NSFileManager *fm = NSFileManager.defaultManager;
    for (NSString *path in @[ self.python, self.firstrun, self.cc, self.lld, self.host ])
        if (![fm isExecutableFileAtPath:path] && ![fm fileExistsAtPath:path])
            return path;
    return nil;
}
@end

static void launcher_log(EEPaths *paths, NSString *line) {
    [NSFileManager.defaultManager createDirectoryAtPath:paths.logs
                            withIntermediateDirectories:YES
                                             attributes:nil
                                                  error:nil];
    NSString *file = [paths.logs stringByAppendingPathComponent:@"launcher.log"];
    NSDateFormatter *f = [NSDateFormatter new];
    f.dateFormat = @"yyyy-MM-dd HH:mm:ss";
    NSString *text = [NSString stringWithFormat:@"%@ %@\n", [f stringFromDate:NSDate.date], line];
    FILE *out = fopen(file.fileSystemRepresentation, "a");
    if (out) {
        fputs(text.UTF8String, out);
        fclose(out);
    }
}

// --------------------------------------------------------- firstrun.py --

typedef void (^EEEventBlock)(NSDictionary *event);
typedef void (^EEDoneBlock)(int status, NSDictionary *result);

// One run of firstrun.py: JSON lines in, events out on the main queue.
@interface EEFirstRun : NSObject
@property(strong) dispatch_queue_t callbacks; // where events arrive (default: main)
@property(strong) NSTask *task;
@property(strong) NSMutableData *pending;
@property(strong) NSDictionary *result;
@property(assign) BOOL stopping;
@end

@implementation EEFirstRun

// The child's environment: ours, minus anything that could point the tools
// at another toolchain, with a PATH that has no Homebrew or developer tools
// (firstrun gets every tool by its full path).
+ (NSDictionary *)environment {
    NSMutableDictionary *env = [NSProcessInfo.processInfo.environment mutableCopy];
    for (NSString *key in env.allKeys)
        if ([key hasPrefix:@"PYTHON"] || [key hasPrefix:@"RECOMP_"] ||
            [@[ @"EE_INNOEXTRACT", @"SDKROOT", @"CPATH", @"C_INCLUDE_PATH",
                @"CPLUS_INCLUDE_PATH", @"OBJC_INCLUDE_PATH", @"LIBRARY_PATH",
                @"CCC_OVERRIDE_OPTIONS", @"__PYVENV_LAUNCHER__" ] containsObject:key])
            [env removeObjectForKey:key];
    env[@"PATH"] = @"/usr/bin:/bin:/usr/sbin:/sbin";
    return env;
}

+ (NSArray *)argumentsFor:(NSString *)command extra:(NSArray *)extra paths:(EEPaths *)paths {
    NSMutableArray *args =
        [@[ @"-I", @"-B", paths.firstrun, @"--home", paths.home, @"--quiet" ] mutableCopy];
    NSString *nice = NSProcessInfo.processInfo.environment[@"EE_FIRSTRUN_NICE"];
    if (nice.length && ([command isEqual:@"build"] || [command isEqual:@"import"]))
        [args addObjectsFromArray:@[ @"--nice", nice ]];
    [args addObject:command];
    [args addObjectsFromArray:extra];
    return args;
}

- (BOOL)run:(NSArray *)args
       paths:(EEPaths *)paths
     onEvent:(EEEventBlock)onEvent
      onDone:(EEDoneBlock)onDone
       error:(NSError **)error {
    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:paths.python];
    task.arguments = args;
    task.environment = [EEFirstRun environment];
    task.currentDirectoryURL = [NSURL fileURLWithPath:@"/"];
    NSPipe *out = [NSPipe pipe];
    task.standardOutput = out;
    task.standardError = [NSFileHandle fileHandleWithNullDevice];
    task.standardInput = [NSFileHandle fileHandleWithNullDevice];
    self.pending = [NSMutableData data];
    self.task = task;
    dispatch_queue_t callbacks = self.callbacks ?: dispatch_get_main_queue();
    __weak EEFirstRun *weakSelf = self;
    dispatch_queue_t lines = dispatch_queue_create("ee.firstrun.lines", DISPATCH_QUEUE_SERIAL);
    dispatch_group_t drained = dispatch_group_create();
    dispatch_group_enter(drained);
    out.fileHandleForReading.readabilityHandler = ^(NSFileHandle *h) {
        NSData *chunk = h.availableData;
        dispatch_sync(lines, ^{
            EEFirstRun *me = weakSelf;
            if (!chunk.length) {
                h.readabilityHandler = nil;
                dispatch_group_leave(drained);
                return;
            }
            [me.pending appendData:chunk];
            for (;;) {
                const char *bytes = me.pending.bytes;
                const void *nl = memchr(bytes, '\n', me.pending.length);
                if (!nl)
                    break;
                NSUInteger n = (const char *)nl - bytes;
                NSData *line = [me.pending subdataWithRange:NSMakeRange(0, n)];
                [me.pending replaceBytesInRange:NSMakeRange(0, n + 1) withBytes:NULL length:0];
                id obj = [NSJSONSerialization JSONObjectWithData:line options:0 error:nil];
                if (![obj isKindOfClass:NSDictionary.class])
                    continue;
                if ([obj[@"event"] isEqual:@"result"])
                    me.result = obj;
                dispatch_async(callbacks, ^{
                    if (onEvent)
                        onEvent(obj);
                });
            }
        });
    };
    task.terminationHandler = ^(NSTask *t) {
        if (g_child_pid == t.processIdentifier)
            g_child_pid = 0;
        // Every line is read before the run counts as done.
        dispatch_group_wait(drained, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC));
        int status = t.terminationReason == NSTaskTerminationReasonUncaughtSignal
                         ? 128 + t.terminationStatus
                         : t.terminationStatus;
        NSDictionary *result = weakSelf.result;
        dispatch_async(callbacks, ^{
            if (onDone)
                onDone(status, result);
        });
    };
    if (![task launchAndReturnError:error])
        return NO;
    g_child_pid = task.processIdentifier;
    return YES;
}

// SIGTERM: firstrun stops its compilers and exits 130; the same command
// continues later. SIGKILL only if it does not stop within `grace` seconds.
- (void)stopWithGrace:(double)grace {
    if (!self.task.running)
        return;
    self.stopping = YES;
    pid_t pid = self.task.processIdentifier;
    kill(pid, SIGTERM);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(grace * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
                       if (self.task.running)
                           kill(pid, SIGKILL);
                   });
}

- (BOOL)running {
    return self.task.running;
}
@end

// firstrun.py COMMAND, synchronously: (exit status, result line or nil).
// Its callbacks run on a queue of its own, so it can be called from anywhere,
// the main queue included, without waiting for itself.
static int firstrun_sync(EEPaths *paths, NSString *command, NSArray *extra, BOOL echo,
                         NSDictionary **result) {
    __block NSDictionary *res = nil;
    __block int status = -1;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    EEFirstRun *run = [EEFirstRun new];
    run.callbacks = dispatch_queue_create("ee.firstrun.sync", DISPATCH_QUEUE_SERIAL);
    NSError *error = nil;
    BOOL started = [run run:[EEFirstRun argumentsFor:command extra:extra paths:paths]
        paths:paths
        onEvent:^(NSDictionary *event) {
            if (echo) {
                NSData *json = [NSJSONSerialization dataWithJSONObject:event options:0 error:nil];
                fwrite(json.bytes, 1, json.length, stdout);
                fputc('\n', stdout);
                fflush(stdout);
            }
        }
        onDone:^(int s, NSDictionary *r) {
            status = s;
            res = r;
            dispatch_semaphore_signal(done);
        }
        error:&error];
    if (!started) {
        fprintf(stderr, "launcher: cannot run %s: %s\n", paths.python.UTF8String,
                error.localizedDescription.UTF8String);
        return -1;
    }
    dispatch_semaphore_wait(done, DISPATCH_TIME_FOREVER);
    if (result)
        *result = res;
    return status;
}

static NSArray *toolchain_args(EEPaths *paths) {
    return @[ @"--cc", paths.cc, @"--lld", paths.lld, @"--kit", paths.kit ];
}

// ---------------------------------------------------------- the game --

// Everything the host needs, from a `ready` status; nil (and *why) if the
// build on disk is not usable after all.
static NSDictionary *host_environment(EEPaths *paths, NSDictionary *status, NSString **why) {
    NSDictionary *data = status[@"data"];
    NSDictionary *current = data[@"build"][@"current"];
    NSFileManager *fm = NSFileManager.defaultManager;
    if (![current isKindOfClass:NSDictionary.class]) {
        *why = @"no build is recorded";
        return nil;
    }
    for (NSString *bundle in current[@"load_order"])
        if (![fm fileExistsAtPath:bundle]) {
            *why = [NSString stringWithFormat:@"%@ is missing", bundle];
            return nil;
        }
    NSString *exe = data[@"game_path"];
    if (![fm fileExistsAtPath:exe]) {
        *why = [NSString stringWithFormat:@"%@ is missing", exe];
        return nil;
    }
    NSMutableDictionary *env = [NSMutableDictionary dictionary];
    env[@"RECOMP_CODE_DIR"] = current[@"dir"];
    if ([fm fileExistsAtPath:current[@"symbols"]])
        env[@"RECOMP_SYMBOLS"] = current[@"symbols"];
    env[@"RECOMP_EXE"] = exe;
    env[@"RECOMP_PROFILE_DIR"] = paths.home;
    env[@"RECOMP_REGISTRY"] = data[@"registry"];
    return env;
}

// Replaces this process with the game host. Only returns on failure.
static void exec_host(EEPaths *paths, NSDictionary *hostEnv, BOOL toLog) {
    NSMutableDictionary *env = [NSProcessInfo.processInfo.environment mutableCopy];
    [env addEntriesFromDictionary:hostEnv];
    // A Launch Services start has no useful working directory; the player's
    // folder is where anything relative would land anyway.
    chdir(paths.home.fileSystemRepresentation);
    if (toLog) {
        // The game's own output, for when something goes wrong: the last run's
        // and the one before.
        [NSFileManager.defaultManager createDirectoryAtPath:paths.logs
                                withIntermediateDirectories:YES
                                                 attributes:nil
                                                      error:nil];
        NSString *log = [paths.logs stringByAppendingPathComponent:@"game.log"];
        NSString *old = [paths.logs stringByAppendingPathComponent:@"game.previous.log"];
        rename(log.fileSystemRepresentation, old.fileSystemRepresentation);
        int fd = open(log.fileSystemRepresentation, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd >= 0) {
            dup2(fd, STDOUT_FILENO);
            dup2(fd, STDERR_FILENO);
            close(fd);
        }
    }
    NSMutableArray *pairs = [NSMutableArray array];
    for (NSString *key in env)
        [pairs addObject:[NSString stringWithFormat:@"%@=%@", key, env[key]]];
    char **envp = calloc(pairs.count + 1, sizeof(char *));
    for (NSUInteger i = 0; i < pairs.count; ++i)
        envp[i] = strdup([pairs[i] UTF8String]);
    const char *host = paths.host.fileSystemRepresentation;
    char *argv[] = {(char *)host, NULL};
    launcher_log(paths, [NSString stringWithFormat:@"starting %@ (code %@)", paths.host,
                                                   hostEnv[@"RECOMP_CODE_DIR"]]);
    // The game gets default signal handling, not the launcher's.
    signal(SIGTERM, SIG_DFL);
    signal(SIGINT, SIG_DFL);
    execve(host, argv, envp);
    int e = errno;
    launcher_log(paths, [NSString stringWithFormat:@"exec %@ failed: %s", paths.host, strerror(e)]);
    fprintf(stderr, "launcher: cannot start %s: %s\n", host, strerror(e));
}

// ------------------------------------------------------------- wording --

// A headline for each of firstrun.py's error codes (firstrun.md §6); its own
// message, which names the file or the folder, goes underneath.
static NSString *headline_for(NSString *code) {
    static NSDictionary *words;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        words = @{
            @"usage" : @"The first run could not start.",
            @"busy" : @"Empire Earth is already being set up in another window.",
            @"not-found" : @"No Empire Earth game files were found there.",
            @"aoc-only" : @"That is The Art of Conquest, not Empire Earth itself.",
            @"wrong-version" : @"That is not GOG's Empire Earth Gold.",
            @"partial-copy" : @"The game folder is incomplete.",
            @"modified-files" : @"Some of the game files have been changed.",
            @"installer-unreadable" : @"The installer cannot be read.",
            @"installer-parts-missing" : @"Parts of the installer are missing.",
            @"installer-tool-missing" : @"This copy of the app is incomplete.",
            @"not-imported" : @"The game files have not been imported yet.",
            @"disk-space" : @"There is not enough free disk space.",
            @"toolchain-missing" : @"This copy of the app is incomplete.",
            @"inputs-mismatch" : @"Building the game failed.",
            @"translate-failed" : @"Building the game failed.",
            @"compile-failed" : @"Building the game failed.",
            @"interrupted" : @"Stopped.",
            @"internal" : @"Something unexpected went wrong.",
        };
    });
    return words[code] ?: @"Something went wrong.";
}

static NSString *advice_for(NSString *code) {
    if ([code isEqual:@"installer-tool-missing"] || [code isEqual:@"toolchain-missing"])
        return @"Download the app again and replace this copy.";
    if ([code isEqual:@"disk-space"])
        return @"Free some space, then try again.";
    if ([code hasSuffix:@"-failed"] || [code isEqual:@"inputs-mismatch"] ||
        [code isEqual:@"internal"])
        return @"Try again. If it keeps failing, send the log (Show Log) to whoever gave you "
               @"the app.";
    if ([code isEqual:@"interrupted"])
        return @"Continue now, or open the app again later: it carries on where it stopped.";
    return @"";
}

static NSString *phase_text(NSString *phase) {
    static NSDictionary *words;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        words = @{
            @"locate" : @"Finding the game files",
            @"extract" : @"Unpacking the GOG installer",
            @"verify" : @"Checking the game files",
            @"copy" : @"Copying the game files",
            @"registry" : @"Setting up your player profile",
            @"preflight" : @"Getting ready",
            @"inputs" : @"Reading the game's code",
            @"translate" : @"Translating the game's code",
            @"compile" : @"Compiling the game for your Mac",
            @"cleanup" : @"Cleaning up",
        };
    });
    return words[phase] ?: @"Finishing";
}

// ------------------------------------------------------------ progress --

// One bar for the whole first run. The import takes its first share (more for
// an installer, which is unpacked); the build the rest, by the weights of
// firstrun.md §6 (inputs 5 %, translate 15 %, compile 78 %, the rest 2 %).
@interface EEProgress : NSObject
@property(assign) BOOL installer, building;
- (double)fraction:(NSDictionary *)event;
@end

@implementation EEProgress
- (double)fraction:(NSDictionary *)event {
    NSString *phase = event[@"phase"];
    double done = [event[@"done"] doubleValue], total = [event[@"total"] doubleValue];
    double part = total > 0 ? MIN(1.0, MAX(0.0, done / total)) : 0.0;
    if ([phase isEqual:@"extract"])
        self.installer = YES;
    const double importShare = self.installer ? 0.15 : 0.03;
    NSArray *order;
    NSDictionary *weights;
    double base = 0.0, share = 1.0;
    if (!self.building) {
        order = @[ @"locate", @"extract", @"verify", @"copy", @"registry", @"publish" ];
        weights = self.installer ? @{@"locate" : @2, @"extract" : @70, @"verify" : @6,
                                     @"copy" : @18, @"registry" : @2, @"publish" : @2}
                                 : @{@"locate" : @5, @"extract" : @0, @"verify" : @20,
                                     @"copy" : @65, @"registry" : @5, @"publish" : @5};
        share = importShare;
    } else {
        order = @[ @"preflight", @"inputs", @"translate", @"compile", @"publish", @"cleanup" ];
        weights = @{@"preflight" : @1, @"inputs" : @5, @"translate" : @15, @"compile" : @78,
                    @"publish" : @0.5, @"cleanup" : @0.5};
        base = importShare;
        share = 1.0 - importShare;
    }
    double sum = 0, before = 0;
    for (NSString *p in order)
        sum += [weights[p] doubleValue];
    for (NSString *p in order) {
        if ([p isEqual:phase])
            break;
        before += [weights[p] doubleValue];
    }
    if (![order containsObject:phase])
        return -1;
    return base + share * (before + part * [weights[phase] doubleValue]) / sum;
}
@end

// ------------------------------------------------------------- the UI --

@class EEController;

// The window's content: also where a file or folder can be dropped.
@interface EEDropView : NSView
@property(weak) EEController *controller;
@end

@interface EEController : NSObject <NSApplicationDelegate, NSWindowDelegate>
@property(strong) EEPaths *paths;
@property(strong) NSWindow *window;
@property(strong) NSTextField *headline, *body, *status, *detail, *errorTitle, *errorBody;
@property(strong) NSStackView *choices, *suggestions, *progressBox, *errorBox, *buttons;
@property(strong) NSProgressIndicator *bar;
@property(strong) NSButton *chooseButton, *cancelButton, *retryButton, *logButton, *quitButton;
@property(strong) EEFirstRun *run, *probeRun;
@property(strong) EEProgress *progress;
@property(strong) NSDictionary *initialStatus; // main()'s status answer
@property(copy) NSString *importPath;   // what the player chose
@property(copy) NSString *initialImport; // --import PATH
@property(copy) NSString *retryAction;   // "import" or "build"
@property(strong) NSDate *started;
@property(strong) NSTimer *clock;
@property(assign) BOOL quitting, rebuilt;
@property(strong) NSString *lastPhaseText;
- (BOOL)acceptsDrop;
- (void)importPath:(NSString *)path;
@end

@implementation EEDropView
- (instancetype)initWithFrame:(NSRect)frame {
    if ((self = [super initWithFrame:frame]))
        [self registerForDraggedTypes:@[ NSPasteboardTypeFileURL ]];
    return self;
}
- (NSString *)droppedPath:(id<NSDraggingInfo>)info {
    NSArray *urls = [info.draggingPasteboard readObjectsForClasses:@[ NSURL.class ]
                                                           options:@{
                                                               NSPasteboardURLReadingFileURLsOnlyKey :
                                                                   @YES
                                                           }];
    NSURL *url = urls.firstObject;
    return url.isFileURL ? url.path : nil;
}
- (NSDragOperation)draggingEntered:(id<NSDraggingInfo>)info {
    return [self.controller acceptsDrop] && [self droppedPath:info] ? NSDragOperationCopy
                                                                     : NSDragOperationNone;
}
- (BOOL)performDragOperation:(id<NSDraggingInfo>)info {
    NSString *path = [self droppedPath:info];
    if (!path || ![self.controller acceptsDrop])
        return NO;
    [self.controller importPath:path];
    return YES;
}
@end

static NSTextField *label(NSString *text, CGFloat size, NSFontWeight weight) {
    NSTextField *f = [NSTextField wrappingLabelWithString:text ?: @""];
    f.font = [NSFont systemFontOfSize:size weight:weight];
    f.selectable = YES;
    f.preferredMaxLayoutWidth = 520;
    return f;
}

static NSButton *button(NSString *title, id target, SEL action) {
    NSButton *b = [NSButton buttonWithTitle:title target:target action:action];
    b.bezelStyle = NSBezelStyleRounded;
    return b;
}

@implementation EEController

- (void)buildWindow {
    NSRect frame = NSMakeRect(0, 0, 600, 380);
    self.window = [[NSWindow alloc]
        initWithContentRect:frame
                  styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                            NSWindowStyleMaskMiniaturizable
                    backing:NSBackingStoreBuffered
                      defer:NO];
    self.window.title = @"Empire Earth";
    self.window.delegate = self;
    self.window.releasedWhenClosed = NO;
    EEDropView *content = [[EEDropView alloc] initWithFrame:frame];
    content.controller = self;
    self.window.contentView = content;

    self.headline = label(@"Welcome to Empire Earth for Mac", 20, NSFontWeightSemibold);
    self.body = label(@"Before the first game, this app turns your own copy of Empire Earth Gold "
                      @"into a game for your Mac. It needs your GOG files once and takes a few "
                      @"minutes. Nothing is changed in the files you choose.",
                      13, NSFontWeightRegular);

    // Choosing the game.
    NSTextField *ask = label(@"Choose your GOG Empire Earth Gold installer or game folder",
                             13, NSFontWeightSemibold);
    NSTextField *hint = label(@"The offline installer (setup_empire_earth_gold….exe, with its "
                              @".bin files beside it), the installed “Empire Earth Gold” folder, "
                              @"or a Wine app that has it. You can also drag it onto this window.",
                              11, NSFontWeightRegular);
    hint.textColor = NSColor.secondaryLabelColor;
    self.chooseButton = button(@"Choose…", self, @selector(choose:));
    self.chooseButton.keyEquivalent = @"\r";
    self.suggestions = [NSStackView stackViewWithViews:@[]];
    self.suggestions.orientation = NSUserInterfaceLayoutOrientationVertical;
    self.suggestions.alignment = NSLayoutAttributeLeading;
    self.choices = [NSStackView stackViewWithViews:@[ ask, hint, self.suggestions, self.chooseButton ]];
    self.choices.orientation = NSUserInterfaceLayoutOrientationVertical;
    self.choices.alignment = NSLayoutAttributeLeading;
    self.choices.spacing = 8;

    // Progress.
    self.status = label(@"", 13, NSFontWeightSemibold);
    self.bar = [NSProgressIndicator new];
    self.bar.style = NSProgressIndicatorStyleBar;
    self.bar.indeterminate = NO;
    self.bar.minValue = 0;
    self.bar.maxValue = 1;
    [self.bar.widthAnchor constraintEqualToConstant:540].active = YES;
    self.detail = label(@"", 11, NSFontWeightRegular);
    self.detail.textColor = NSColor.secondaryLabelColor;
    self.progressBox = [NSStackView stackViewWithViews:@[ self.status, self.bar, self.detail ]];
    self.progressBox.orientation = NSUserInterfaceLayoutOrientationVertical;
    self.progressBox.alignment = NSLayoutAttributeLeading;

    // Errors.
    self.errorTitle = label(@"", 13, NSFontWeightSemibold);
    self.errorTitle.textColor = NSColor.systemRedColor;
    self.errorBody = label(@"", 12, NSFontWeightRegular);
    self.errorBox = [NSStackView stackViewWithViews:@[ self.errorTitle, self.errorBody ]];
    self.errorBox.orientation = NSUserInterfaceLayoutOrientationVertical;
    self.errorBox.alignment = NSLayoutAttributeLeading;

    self.retryButton = button(@"Try Again", self, @selector(retry:));
    self.logButton = button(@"Show Log", self, @selector(showLog:));
    self.cancelButton = button(@"Cancel", self, @selector(cancel:));
    self.quitButton = button(@"Quit", NSApp, @selector(terminate:));
    NSView *spacer = [NSView new];
    [spacer setContentHuggingPriority:1 forOrientation:NSLayoutConstraintOrientationHorizontal];
    self.buttons = [NSStackView stackViewWithViews:@[
        self.logButton, spacer, self.retryButton, self.cancelButton, self.quitButton
    ]];
    self.buttons.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    [self.buttons.widthAnchor constraintEqualToConstant:552].active = YES;

    NSStackView *all = [NSStackView stackViewWithViews:@[
        self.headline, self.body, self.choices, self.progressBox, self.errorBox
    ]];
    all.orientation = NSUserInterfaceLayoutOrientationVertical;
    all.alignment = NSLayoutAttributeLeading;
    all.spacing = 14;
    all.translatesAutoresizingMaskIntoConstraints = NO;
    self.buttons.translatesAutoresizingMaskIntoConstraints = NO;
    [content addSubview:all];
    [content addSubview:self.buttons];
    [NSLayoutConstraint activateConstraints:@[
        [all.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:24],
        [all.trailingAnchor constraintLessThanOrEqualToAnchor:content.trailingAnchor constant:-24],
        [all.topAnchor constraintEqualToAnchor:content.topAnchor constant:22],
        [self.buttons.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:24],
        [self.buttons.bottomAnchor constraintEqualToAnchor:content.bottomAnchor constant:-18],
    ]];
    [self.window center];
}

- (void)show:(NSString *)mode {
    // mode: choose, running, error, stopped
    self.choices.hidden = ![mode isEqual:@"choose"] && ![mode isEqual:@"error-choose"];
    self.progressBox.hidden = ![mode isEqual:@"running"] && ![mode isEqual:@"stopped"];
    self.errorBox.hidden = ![mode hasPrefix:@"error"] && ![mode isEqual:@"stopped"];
    self.cancelButton.hidden = ![mode isEqual:@"running"];
    self.retryButton.hidden = ![mode isEqual:@"error"] && ![mode isEqual:@"stopped"];
    self.retryButton.title = [mode isEqual:@"stopped"] ? @"Continue" : @"Try Again";
    self.logButton.hidden = [mode isEqual:@"choose"];
    self.quitButton.hidden = [mode isEqual:@"running"];
}

- (void)applicationDidFinishLaunching:(NSNotification *)note {
    [self buildWindow];
    NSString *broken = [self.paths missing];
    if (broken) {
        [self fail:@"toolchain-missing"
              message:[NSString stringWithFormat:@"%@ is missing from the app.", broken]
                 data:nil
               choose:NO];
        [self.window makeKeyAndOrderFront:nil];
        [NSApp activateIgnoringOtherApps:YES];
        return;
    }
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    [self refresh];
}

// Where things stand: start the game, carry on building, or ask for the game.
- (void)refresh {
    NSDictionary *res = self.initialStatus;
    int rc = 0;
    self.initialStatus = nil;
    if (!res) {
        rc = firstrun_sync(self.paths, @"status", toolchain_args(self.paths), NO, &res);
        launcher_log(self.paths, [NSString stringWithFormat:@"status: %@ (exit %d)",
                                                           res[@"data"][@"state"], rc]);
    }
    NSString *state = res[@"data"][@"state"];
    if (rc != 0 || !state) {
        [self fail:res[@"data"][@"code"] ?: @"internal"
              message:res[@"message"] ?: @"The first-run tools did not answer."
                 data:nil
               choose:NO];
        return;
    }
    if ([state isEqual:@"ready"]) {
        [self startGame:res];
        return;
    }
    if (![state isEqual:@"needs-import"]) {
        [self startBuild:[state isEqual:@"build-resumable"]];
        return;
    }
    if (self.initialImport) {
        NSString *path = self.initialImport;
        self.initialImport = nil;
        [self importPath:path];
        return;
    }
    [self show:@"choose"];
    [self probe];
}

// What `probe` finds becomes one click each.
- (void)probe {
    for (NSView *v in self.suggestions.arrangedSubviews)
        [v removeFromSuperview];
    NSTextField *looking = label(@"Looking for your game…", 11, NSFontWeightRegular);
    looking.textColor = NSColor.secondaryLabelColor;
    [self.suggestions addArrangedSubview:looking];
    NSMutableArray *extra = [NSMutableArray array];
    NSString *paths = NSProcessInfo.processInfo.environment[@"EE_PROBE_PATHS"];
    if (paths.length)
        [extra addObjectsFromArray:[paths componentsSeparatedByString:@":"]];
    EEFirstRun *probe = [EEFirstRun new];
    __weak EEController *weakSelf = self;
    self.probeRun = probe;
    [probe run:[EEFirstRun argumentsFor:@"probe" extra:extra paths:self.paths]
          paths:self.paths
        onEvent:nil
         onDone:^(int status, NSDictionary *result) {
             EEController *me = weakSelf;
             me.probeRun = nil;
             if (!me.choices.hidden)
                 [me showSuggestions:result[@"data"][@"candidates"]];
         }
          error:nil];
}

- (void)showSuggestions:(NSArray *)candidates {
    for (NSView *v in self.suggestions.arrangedSubviews)
        [v removeFromSuperview];
    NSUInteger shown = 0;
    for (NSDictionary *c in candidates) {
        if ([c[@"ok"] isEqual:@NO] || ![c[@"path"] isKindOfClass:NSString.class])
            continue;
        NSString *kind = c[@"kind"];
        NSString *what = [kind isEqual:@"installer"] ? @"GOG installer"
                         : [kind isEqual:@"wrapper"] ? @"Wine app"
                                                      : @"game folder";
        NSString *title = [NSString
            stringWithFormat:@"Use %@ (%@)", [c[@"path"] lastPathComponent], what];
        NSButton *b = button(title, self, @selector(useSuggestion:));
        b.toolTip = c[@"path"];
        b.identifier = c[@"path"];
        [self.suggestions addArrangedSubview:b];
        if (++shown == 4)
            break;
    }
    if (!shown) {
        NSTextField *none = label(@"No copy of the game was found in the usual places.", 11,
                                  NSFontWeightRegular);
        none.textColor = NSColor.secondaryLabelColor;
        [self.suggestions addArrangedSubview:none];
    }
}

- (void)useSuggestion:(NSButton *)sender {
    [self importPath:sender.identifier];
}

- (BOOL)acceptsDrop {
    return !self.run.running && !self.choices.hidden;
}

- (void)choose:(id)sender {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = YES;
    panel.canChooseDirectories = YES;
    panel.allowsMultipleSelection = NO;
    panel.treatsFilePackagesAsDirectories = NO;
    panel.message = @"Choose setup_empire_earth_gold….exe, the “Empire Earth Gold” or “Empire "
                    @"Earth” folder, or a Wine app with the game";
    panel.prompt = @"Use";
    [panel beginSheetModalForWindow:self.window
                  completionHandler:^(NSModalResponse r) {
                      if (r == NSModalResponseOK && panel.URL.path)
                          [self importPath:panel.URL.path];
                  }];
}

- (void)begin:(NSString *)text {
    self.started = NSDate.date;
    self.status.stringValue = text;
    self.detail.stringValue = @"";
    [self.clock invalidate];
    __weak EEController *weakSelf = self;
    self.clock = [NSTimer scheduledTimerWithTimeInterval:1.0
                                                 repeats:YES
                                                   block:^(NSTimer *t) {
                                                       [weakSelf tick];
                                                   }];
    [self show:@"running"];
}

- (void)tick {
    if (!self.run.running || !self.lastPhaseText)
        return;
    int s = (int)-[self.started timeIntervalSinceNow];
    self.status.stringValue = [NSString stringWithFormat:@"%@ — %d:%02d", self.lastPhaseText,
                                                         s / 60, s % 60];
}

- (void)onEvent:(NSDictionary *)event {
    NSString *kind = event[@"event"];
    if ([kind isEqual:@"progress"]) {
        double f = [self.progress fraction:event];
        if (f >= 0)
            self.bar.doubleValue = MAX(self.bar.doubleValue, f);
        NSString *step = self.progress.building ? @"Step 2 of 2: " : @"Step 1 of 2: ";
        self.lastPhaseText = [step stringByAppendingString:phase_text(event[@"phase"])];
        [self tick];
        NSString *message = event[@"message"] ?: @"";
        long done = [event[@"done"] longValue], total = [event[@"total"] longValue];
        NSString *unit = event[@"unit"];
        if (total > 1 && ([unit isEqual:@"objects"] || [unit isEqual:@"modules"] ||
                          [unit isEqual:@"files"]))
            message = [NSString stringWithFormat:@"%@ (%ld of %ld)", message, done, total];
        self.detail.stringValue = message;
    }
}

- (void)importPath:(NSString *)path {
    self.importPath = path;
    self.retryAction = @"import";
    self.progress = [EEProgress new];
    self.bar.doubleValue = 0;
    [self begin:@"Step 1 of 2: Importing your game files"];
    self.headline.stringValue = @"Setting up Empire Earth";
    launcher_log(self.paths, [NSString stringWithFormat:@"import %@", path]);
    [self launch:@"import"
            extra:@[ path, @"--innoextract", self.paths.innoextract ]
             then:^(int status, NSDictionary *result) {
                 if (status == 0)
                     [self startBuild:NO];
                 else
                     [self failed:status result:result choose:YES];
             }];
}

- (void)startBuild:(BOOL)resuming {
    self.retryAction = @"build";
    if (!self.progress) {
        self.progress = [EEProgress new];
        self.bar.doubleValue = 0;
    }
    self.progress.building = YES;
    [self begin:resuming ? @"Step 2 of 2: Continuing where it stopped"
                         : @"Step 2 of 2: Building the game for your Mac"];
    self.headline.stringValue = @"Setting up Empire Earth";
    self.body.stringValue = @"This happens once and takes a few minutes. You can quit at any "
                            @"time: it carries on where it stopped the next time you open the "
                            @"app.";
    launcher_log(self.paths, resuming ? @"build (resuming)" : @"build");
    NSMutableArray *extra = [toolchain_args(self.paths) mutableCopy];
    NSString *jobs = NSProcessInfo.processInfo.environment[@"EE_FIRSTRUN_JOBS"];
    if (jobs.length)
        [extra addObjectsFromArray:@[ @"--jobs", jobs ]];
    [self launch:@"build"
            extra:extra
             then:^(int status, NSDictionary *result) {
                 if (status == 0) {
                     self.bar.doubleValue = 1;
                     self.status.stringValue = @"Ready. Starting Empire Earth…";
                     self.detail.stringValue = @"";
                     dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 2),
                                    dispatch_get_main_queue(), ^{
                                        [self refresh];
                                    });
                 } else {
                     [self failed:status result:result choose:NO];
                 }
             }];
}

- (void)launch:(NSString *)command extra:(NSArray *)extra then:(EEDoneBlock)then {
    EEFirstRun *run = [EEFirstRun new];
    self.run = run;
    __weak EEController *weakSelf = self;
    NSError *error = nil;
    BOOL ok = [run run:[EEFirstRun argumentsFor:command extra:extra paths:self.paths]
        paths:self.paths
        onEvent:^(NSDictionary *event) {
            [weakSelf onEvent:event];
        }
        onDone:^(int status, NSDictionary *result) {
            EEController *me = weakSelf;
            [me.clock invalidate];
            launcher_log(me.paths, [NSString stringWithFormat:@"%@: exit %d %@", command, status,
                                                              result[@"message"] ?: @""]);
            if (me.quitting) {
                [NSApp replyToApplicationShouldTerminate:YES];
                return;
            }
            then(status, result);
        }
        error:&error];
    if (!ok)
        [self fail:@"toolchain-missing" message:error.localizedDescription data:nil choose:NO];
}

- (void)failed:(int)status result:(NSDictionary *)result choose:(BOOL)choose {
    NSString *code = result[@"data"][@"code"];
    if (status == 130 || [code isEqual:@"interrupted"]) {
        self.errorTitle.stringValue = headline_for(@"interrupted");
        self.errorBody.stringValue = advice_for(@"interrupted");
        self.errorTitle.textColor = NSColor.labelColor;
        [self show:@"stopped"];
        return;
    }
    [self fail:code ?: @"internal"
         message:result[@"message"] ?: [NSString stringWithFormat:@"The first run ended with "
                                                                  @"status %d.",
                                                                  status]
            data:result[@"data"]
          choose:choose];
}

- (void)fail:(NSString *)code message:(NSString *)message data:(NSDictionary *)data choose:(BOOL)choose {
    [self.clock invalidate];
    self.errorTitle.textColor = NSColor.systemRedColor;
    self.errorTitle.stringValue = headline_for(code);
    NSString *advice = advice_for(code);
    self.errorBody.stringValue =
        advice.length ? [NSString stringWithFormat:@"%@\n\n%@", message, advice] : message;
    // A problem with what was chosen: choose again. Anything else: try again.
    BOOL pick = choose && ![code isEqual:@"busy"] && ![code isEqual:@"disk-space"] &&
                ![code isEqual:@"installer-tool-missing"];
    [self show:pick ? @"error-choose" : @"error"];
    if (pick)
        [self probe];
}

- (void)retry:(id)sender {
    if ([self.retryAction isEqual:@"import"] && self.importPath)
        [self importPath:self.importPath];
    else
        [self refresh];
}

- (void)cancel:(id)sender {
    self.detail.stringValue = @"Stopping…";
    [self.run stopWithGrace:15];
}

- (void)showLog:(id)sender {
    NSString *log = [self.paths.home stringByAppendingPathComponent:@"Build/firstrun.log"];
    if (![NSFileManager.defaultManager fileExistsAtPath:log])
        log = [self.paths.logs stringByAppendingPathComponent:@"launcher.log"];
    [NSWorkspace.sharedWorkspace activateFileViewerSelectingURLs:@[ [NSURL fileURLWithPath:log] ]];
}

- (void)startGame:(NSDictionary *)status {
    NSString *why = nil;
    NSDictionary *env = host_environment(self.paths, status, &why);
    if (!env) {
        launcher_log(self.paths, [NSString stringWithFormat:@"not usable: %@", why]);
        if (![NSFileManager.defaultManager fileExistsAtPath:status[@"data"][@"game_path"]]) {
            [self fail:@"not-imported"
                 message:@"The imported game files are missing from your Empire Earth folder. "
                         @"Choose your game again."
                    data:nil
                  choose:YES];
        } else if (!self.rebuilt) {
            self.rebuilt = YES;
            [self startBuild:NO];
        } else {
            [self fail:@"internal" message:why data:nil choose:NO];
        }
        return;
    }
    [self.window orderOut:nil];
    exec_host(self.paths, env, YES);
    [self fail:@"internal"
         message:[NSString stringWithFormat:@"The game could not be started (%@).", self.paths.host]
            data:nil
          choose:NO];
    [self.window makeKeyAndOrderFront:nil];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)app {
    return YES;
}

// Quitting while it works: stop it so it can carry on next time, then quit.
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {
    if (!self.run.running)
        return NSTerminateNow;
    self.quitting = YES;
    self.detail.stringValue = @"Stopping…";
    [self.run stopWithGrace:15];
    return NSTerminateLater;
}
@end

// ----------------------------------------------------------- no window --

static int no_ui(EEPaths *paths, NSString *importPath, BOOL play) {
    signal(SIGINT, forward_stop);
    signal(SIGTERM, forward_stop);
    NSString *broken = [paths missing];
    if (broken) {
        fprintf(stderr, "launcher: %s is missing from the app\n", broken.UTF8String);
        return 1;
    }
    NSDictionary *status = nil;
    int rc = firstrun_sync(paths, @"status", toolchain_args(paths), NO, &status);
    NSString *state = status[@"data"][@"state"];
    fprintf(stderr, "launcher: home %s: %s\n", paths.home.UTF8String,
            state ? state.UTF8String : "(no answer)");
    if (rc != 0 || !state)
        return 1;
    if ([state isEqual:@"needs-import"]) {
        if (!importPath) {
            NSDictionary *probe = nil;
            NSString *probePaths = NSProcessInfo.processInfo.environment[@"EE_PROBE_PATHS"];
            NSArray *extra = probePaths.length ? [probePaths componentsSeparatedByString:@":"] : @[];
            firstrun_sync(paths, @"probe", extra, YES, &probe);
            for (NSDictionary *c in probe[@"data"][@"candidates"])
                if ([c[@"ok"] isEqual:@YES]) {
                    importPath = c[@"path"];
                    break;
                }
            if (!importPath) {
                fprintf(stderr, "launcher: no usable game found; pass --import PATH\n");
                return 1;
            }
        }
        fprintf(stderr, "launcher: importing %s\n", importPath.UTF8String);
        rc = firstrun_sync(paths, @"import", @[ importPath, @"--innoextract", paths.innoextract ],
                           YES, nil);
        if (rc != 0)
            return rc;
        state = @"needs-build";
    }
    if (![state isEqual:@"ready"]) {
        NSMutableArray *extra = [toolchain_args(paths) mutableCopy];
        NSString *jobs = NSProcessInfo.processInfo.environment[@"EE_FIRSTRUN_JOBS"];
        if (jobs.length)
            [extra addObjectsFromArray:@[ @"--jobs", jobs ]];
        fprintf(stderr, "launcher: building\n");
        rc = firstrun_sync(paths, @"build", extra, YES, nil);
        if (rc != 0)
            return rc;
        rc = firstrun_sync(paths, @"status", toolchain_args(paths), NO, &status);
        state = status[@"data"][@"state"];
        if (rc != 0 || ![state isEqual:@"ready"]) {
            fprintf(stderr, "launcher: built, but the state is %s\n",
                    state ? state.UTF8String : "(no answer)");
            return 1;
        }
    }
    NSString *why = nil;
    NSDictionary *env = host_environment(paths, status, &why);
    if (!env) {
        fprintf(stderr, "launcher: the build is not usable: %s\n", why.UTF8String);
        return 1;
    }
    for (NSString *key in [env.allKeys sortedArrayUsingSelector:@selector(compare:)])
        fprintf(stderr, "launcher: %s=%s\n", key.UTF8String, [env[key] UTF8String]);
    if (!play) {
        fprintf(stderr, "launcher: ready (--no-play)\n");
        return 0;
    }
    exec_host(paths, env, NO);
    return 1;
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        BOOL ui = YES, play = YES, statusOnly = NO;
        NSString *importPath = nil;
        for (int i = 1; i < argc; ++i) {
            if (!strcmp(argv[i], "--no-ui"))
                ui = NO;
            else if (!strcmp(argv[i], "--no-play"))
                play = NO;
            else if (!strcmp(argv[i], "--status"))
                statusOnly = YES;
            else if (!strcmp(argv[i], "--import") && i + 1 < argc)
                importPath = @(argv[++i]);
            else if (!strncmp(argv[i], "-psn_", 5))
                continue; // what an old Launch Services adds
            else if (!strcmp(argv[i], "-NSDocumentRevisionsDebugMode") && i + 1 < argc)
                ++i; // what Xcode adds
            else {
                fprintf(stderr, "usage: %s [--no-ui] [--import PATH] [--no-play] [--status]\n",
                        argv[0]);
                return 2;
            }
        }
        EEPaths *paths = [EEPaths current];
        if (statusOnly) {
            NSDictionary *status = nil;
            int rc = firstrun_sync(paths, @"status", toolchain_args(paths), NO, &status);
            if (status) {
                NSData *json = [NSJSONSerialization dataWithJSONObject:status options:0 error:nil];
                fwrite(json.bytes, 1, json.length, stdout);
                fputc('\n', stdout);
            }
            return rc;
        }
        if (!ui)
            return no_ui(paths, importPath, play);

        // Ready: straight into the game, no window.
        NSDictionary *status = nil;
        if (![paths missing]) {
            int rc = firstrun_sync(paths, @"status", toolchain_args(paths), NO, &status);
            NSString *state = status[@"data"][@"state"];
            launcher_log(paths, [NSString stringWithFormat:@"status: %@ (exit %d)", state, rc]);
            if (rc == 0 && [state isEqual:@"ready"]) {
                NSString *why = nil;
                NSDictionary *env = host_environment(paths, status, &why);
                if (env)
                    exec_host(paths, env, YES);
                else
                    launcher_log(paths, [NSString stringWithFormat:@"not usable: %@", why]);
                status = nil;
            } else if (rc != 0) {
                status = nil;
            }
        }

        NSApplication *app = NSApplication.sharedApplication;
        app.activationPolicy = NSApplicationActivationPolicyRegular;
        // The menu bar: the application menu with Quit, and Edit for copying
        // an error message.
        NSMenu *bar = [NSMenu new];
        NSMenuItem *appItem = [NSMenuItem new];
        NSMenu *appMenu = [NSMenu new];
        [appMenu addItemWithTitle:@"Quit Empire Earth" action:@selector(terminate:) keyEquivalent:@"q"];
        appItem.submenu = appMenu;
        [bar addItem:appItem];
        NSMenuItem *editItem = [NSMenuItem new];
        NSMenu *edit = [[NSMenu alloc] initWithTitle:@"Edit"];
        [edit addItemWithTitle:@"Copy" action:@selector(copy:) keyEquivalent:@"c"];
        [edit addItemWithTitle:@"Select All" action:@selector(selectAll:) keyEquivalent:@"a"];
        editItem.submenu = edit;
        [bar addItem:editItem];
        app.mainMenu = bar;

        EEController *controller = [EEController new];
        controller.paths = paths;
        // A SIGTERM (logout, `kill`) quits the way the Quit command does: a
        // running first run is stopped so the next start resumes it.
        signal(SIGTERM, SIG_IGN);
        dispatch_source_t term =
            dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, SIGTERM, 0, dispatch_get_main_queue());
        dispatch_source_set_event_handler(term, ^{
            [NSApp terminate:nil];
        });
        dispatch_resume(term);
        controller.initialImport = importPath;
        controller.initialStatus = status;
        app.delegate = controller;
        [app run];
    }
    return 0;
}
