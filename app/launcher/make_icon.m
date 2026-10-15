// make_icon.m - draws the app's icon (our own artwork: a stone tower on a
// blue tile; nothing from the game) into an .iconset for iconutil.
//
//   make_icon OUT.iconset        (tools/package_app.py builds and runs it)
#import <AppKit/AppKit.h>

static void draw(CGContextRef g, CGFloat s) {
    // macOS's icon grid: the tile is 824/1024 of the canvas, centred.
    CGFloat inset = s * 100.0 / 1024.0, side = s - 2 * inset, r = side * 0.225;
    CGRect tile = CGRectMake(inset, inset, side, side);
    CGPathRef shape = CGPathCreateWithRoundedRect(tile, r, r, NULL);

    // Shadow under the tile.
    CGContextSaveGState(g);
    CGContextSetShadowWithColor(g, CGSizeMake(0, -s * 0.012), s * 0.02,
                                [NSColor colorWithWhite:0 alpha:0.35].CGColor);
    CGContextAddPath(g, shape);
    CGContextSetFillColorWithColor(g, [NSColor colorWithSRGBRed:0.07 green:0.17 blue:0.36 alpha:1].CGColor);
    CGContextFillPath(g);
    CGContextRestoreGState(g);

    // The tile: evening sky, deep blue at the top to teal at the horizon.
    CGContextSaveGState(g);
    CGContextAddPath(g, shape);
    CGContextClip(g);
    CGColorSpaceRef rgb = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGFloat sky[] = {0.10, 0.36, 0.52, 1, 0.06, 0.13, 0.33, 1};
    CGGradientRef gradient = CGGradientCreateWithColorComponents(rgb, sky, NULL, 2);
    CGContextDrawLinearGradient(g, gradient, CGPointMake(0, inset), CGPointMake(0, inset + side), 0);
    CGGradientRelease(gradient);

    // A low sun behind the tower.
    CGFloat sunR = side * 0.20;
    CGPoint sun = CGPointMake(s * 0.5, inset + side * 0.40);
    CGFloat glow[] = {1.0, 0.82, 0.45, 0.9, 1.0, 0.70, 0.30, 0.0};
    CGGradientRef halo = CGGradientCreateWithColorComponents(rgb, glow, NULL, 2);
    CGContextDrawRadialGradient(g, halo, sun, sunR * 0.9, sun, sunR * 2.6, 0);
    CGGradientRelease(halo);
    CGContextSetFillColorWithColor(g, [NSColor colorWithSRGBRed:1.0 green:0.86 blue:0.52 alpha:1].CGColor);
    CGContextFillEllipseInRect(g, CGRectMake(sun.x - sunR, sun.y - sunR, 2 * sunR, 2 * sunR));

    // Hills.
    CGContextSetFillColorWithColor(g, [NSColor colorWithSRGBRed:0.05 green:0.22 blue:0.20 alpha:1].CGColor);
    CGContextMoveToPoint(g, inset, inset + side * 0.30);
    CGContextAddCurveToPoint(g, inset + side * 0.30, inset + side * 0.40, inset + side * 0.62,
                             inset + side * 0.22, inset + side, inset + side * 0.33);
    CGContextAddLineToPoint(g, inset + side, inset);
    CGContextAddLineToPoint(g, inset, inset);
    CGContextClosePath(g);
    CGContextFillPath(g);

    // The tower: a stone keep with four merlons, a window and a gate.
    CGFloat w = side * 0.30, h = side * 0.46, x = s * 0.5 - w / 2, y = inset + side * 0.16;
    CGFloat merlonW = w / 7, merlonH = side * 0.06;
    CGMutablePathRef tower = CGPathCreateMutable();
    CGPathMoveToPoint(tower, NULL, x - side * 0.02, y);
    CGPathAddLineToPoint(tower, NULL, x + w + side * 0.02, y);
    CGPathAddLineToPoint(tower, NULL, x + w, y + h);
    for (int i = 3; i >= 0; --i) {
        CGFloat mx = x + merlonW * (2 * i);
        CGPathAddLineToPoint(tower, NULL, mx + merlonW, y + h);
        CGPathAddLineToPoint(tower, NULL, mx + merlonW, y + h + merlonH);
        CGPathAddLineToPoint(tower, NULL, mx, y + h + merlonH);
        CGPathAddLineToPoint(tower, NULL, mx, y + h);
        if (i)
            CGPathAddLineToPoint(tower, NULL, mx - merlonW, y + h);
    }
    CGPathCloseSubpath(tower);
    CGFloat stone[] = {0.93, 0.82, 0.58, 1, 0.70, 0.55, 0.33, 1};
    CGGradientRef stoneGradient = CGGradientCreateWithColorComponents(rgb, stone, NULL, 2);
    CGContextSaveGState(g);
    CGContextAddPath(g, tower);
    CGContextClip(g);
    CGContextDrawLinearGradient(g, stoneGradient, CGPointMake(x, 0), CGPointMake(x + w, 0), 0);
    CGContextRestoreGState(g);
    CGGradientRelease(stoneGradient);
    CGContextSetStrokeColorWithColor(g, [NSColor colorWithSRGBRed:0.35 green:0.24 blue:0.12 alpha:1].CGColor);
    CGContextSetLineWidth(g, s * 0.008);
    CGContextSetLineJoin(g, kCGLineJoinRound);
    CGContextAddPath(g, tower);
    CGContextStrokePath(g);
    CGPathRelease(tower);

    // Courses of stone.
    CGContextSetStrokeColorWithColor(g, [NSColor colorWithSRGBRed:0.55 green:0.40 blue:0.22 alpha:0.6].CGColor);
    CGContextSetLineWidth(g, s * 0.004);
    for (int i = 1; i < 6; ++i) {
        CGFloat cy = y + h * i / 6.0;
        CGFloat slope = side * 0.02 * (1 - (cy - y) / h);
        CGContextMoveToPoint(g, x - slope, cy);
        CGContextAddLineToPoint(g, x + w + slope, cy);
    }
    CGContextStrokePath(g);

    // The gate (an arch) and a window.
    CGColorRef dark = [NSColor colorWithSRGBRed:0.16 green:0.10 blue:0.06 alpha:1].CGColor;
    CGContextSetFillColorWithColor(g, dark);
    CGFloat gw = w * 0.36, gh = h * 0.30, gx = s * 0.5 - gw / 2;
    CGContextMoveToPoint(g, gx, y);
    CGContextAddLineToPoint(g, gx, y + gh - gw / 2);
    CGContextAddArc(g, s * 0.5, y + gh - gw / 2, gw / 2, M_PI, 0, 1);
    CGContextAddLineToPoint(g, gx + gw, y);
    CGContextClosePath(g);
    CGContextFillPath(g);
    CGFloat ww = w * 0.16, wh = h * 0.16, wy = y + h * 0.62;
    CGContextMoveToPoint(g, s * 0.5 - ww / 2, wy);
    CGContextAddLineToPoint(g, s * 0.5 - ww / 2, wy + wh - ww / 2);
    CGContextAddArc(g, s * 0.5, wy + wh - ww / 2, ww / 2, M_PI, 0, 1);
    CGContextAddLineToPoint(g, s * 0.5 + ww / 2, wy);
    CGContextClosePath(g);
    CGContextSetFillColorWithColor(g, [NSColor colorWithSRGBRed:1.0 green:0.78 blue:0.35 alpha:1].CGColor);
    CGContextFillPath(g);

    // A pennant on a pole.
    CGFloat px = s * 0.5, py = y + h + merlonH;
    CGContextSetStrokeColorWithColor(g, [NSColor colorWithSRGBRed:0.30 green:0.22 blue:0.14 alpha:1].CGColor);
    CGContextSetLineWidth(g, s * 0.010);
    CGContextMoveToPoint(g, px, py);
    CGContextAddLineToPoint(g, px, py + side * 0.15);
    CGContextStrokePath(g);
    CGContextSetFillColorWithColor(g, [NSColor colorWithSRGBRed:0.80 green:0.16 blue:0.14 alpha:1].CGColor);
    CGContextMoveToPoint(g, px, py + side * 0.15);
    CGContextAddCurveToPoint(g, px + side * 0.06, py + side * 0.16, px + side * 0.09, py + side * 0.11,
                             px + side * 0.14, py + side * 0.12);
    CGContextAddCurveToPoint(g, px + side * 0.09, py + side * 0.08, px + side * 0.05, py + side * 0.11,
                             px, py + side * 0.095);
    CGContextClosePath(g);
    CGContextFillPath(g);

    CGColorSpaceRelease(rgb);
    CGContextRestoreGState(g);

    // A thin edge to lift the tile off light backgrounds.
    CGContextAddPath(g, shape);
    CGContextSetStrokeColorWithColor(g, [NSColor colorWithWhite:1 alpha:0.12].CGColor);
    CGContextSetLineWidth(g, s * 0.004);
    CGContextStrokePath(g);
    CGPathRelease(shape);
}

static BOOL write_png(NSString *path, int pixels) {
    CGColorSpaceRef rgb = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGContextRef g = CGBitmapContextCreate(NULL, pixels, pixels, 8, 0, rgb,
                                           kCGImageAlphaPremultipliedLast);
    CGColorSpaceRelease(rgb);
    CGContextSetShouldAntialias(g, true);
    CGContextSetInterpolationQuality(g, kCGInterpolationHigh);
    draw(g, pixels);
    CGImageRef image = CGBitmapContextCreateImage(g);
    CGContextRelease(g);
    NSBitmapImageRep *rep = [[NSBitmapImageRep alloc] initWithCGImage:image];
    CGImageRelease(image);
    NSData *png = [rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
    return [png writeToFile:path atomically:YES];
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 2) {
            fprintf(stderr, "usage: %s OUT.iconset\n", argv[0]);
            return 2;
        }
        NSString *dir = @(argv[1]);
        [NSFileManager.defaultManager createDirectoryAtPath:dir
                                withIntermediateDirectories:YES
                                                 attributes:nil
                                                      error:nil];
        const int sizes[] = {16, 32, 128, 256, 512};
        for (int i = 0; i < 5; ++i) {
            int s = sizes[i];
            NSString *one = [dir stringByAppendingPathComponent:
                                     [NSString stringWithFormat:@"icon_%dx%d.png", s, s]];
            NSString *two = [dir stringByAppendingPathComponent:
                                     [NSString stringWithFormat:@"icon_%dx%d@2x.png", s, s]];
            if (!write_png(one, s) || !write_png(two, 2 * s)) {
                fprintf(stderr, "make_icon: cannot write %s\n", dir.UTF8String);
                return 1;
            }
        }
    }
    return 0;
}
