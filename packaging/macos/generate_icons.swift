#!/usr/bin/env swift

import AppKit
import Foundation

private let fileManager = FileManager.default
private let scriptURL = URL(fileURLWithPath: #filePath)
private let repositoryRoot = scriptURL
    .deletingLastPathComponent()
    .deletingLastPathComponent()
    .deletingLastPathComponent()
private let logoURL = repositoryRoot.appendingPathComponent("bridge/web/assets/dotii.svg")
private let assetCatalogURL = repositoryRoot
    .appendingPathComponent("macos/DotiiManagementCenter/DotiiManagementCenter/Assets.xcassets")
private let appIconURL = assetCatalogURL.appendingPathComponent("AppIcon.appiconset")
private let statusIconURL = assetCatalogURL.appendingPathComponent("StatusBarIcon.imageset")

private func bitmap(size: Int, draw: (CGContext, CGFloat) -> Void) throws -> NSBitmapImageRep {
    guard let representation = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: size,
        pixelsHigh: size,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .calibratedRGB,
        bitmapFormat: [],
        bytesPerRow: 0,
        bitsPerPixel: 0
    ), let context = NSGraphicsContext(bitmapImageRep: representation)?.cgContext else {
        throw NSError(domain: "DotiiIcons", code: 1, userInfo: [NSLocalizedDescriptionKey: "无法创建图标画布"])
    }
    context.interpolationQuality = .high
    draw(context, CGFloat(size))
    return representation
}

private func writePNG(_ representation: NSBitmapImageRep, to url: URL) throws {
    guard let data = representation.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "DotiiIcons", code: 2, userInfo: [NSLocalizedDescriptionKey: "无法编码 PNG"])
    }
    try data.write(to: url, options: .atomic)
}

private func drawAppIcon(logo: NSImage, size: Int) throws -> NSBitmapImageRep {
    try bitmap(size: size) { context, side in
        let scale = side / 1024
        let colors = [
            NSColor(calibratedRed: 0.055, green: 0.102, blue: 0.153, alpha: 1).cgColor,
            NSColor(calibratedRed: 0.020, green: 0.061, blue: 0.082, alpha: 1).cgColor,
        ] as CFArray
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let gradient = CGGradient(colorsSpace: colorSpace, colors: colors, locations: [0, 1])!
        context.drawLinearGradient(
            gradient,
            start: CGPoint(x: side * 0.18, y: side),
            end: CGPoint(x: side * 0.82, y: 0),
            options: [.drawsBeforeStartLocation, .drawsAfterEndLocation]
        )

        let logoRect = CGRect(x: -128 * scale, y: -112 * scale, width: 1280 * scale, height: 1280 * scale)
        context.saveGState()
        context.setShadow(offset: CGSize(width: 0, height: -12 * scale), blur: 24 * scale,
                          color: NSColor.black.withAlphaComponent(0.34).cgColor)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
        logo.draw(in: logoRect, from: .zero, operation: .sourceOver, fraction: 1)
        NSGraphicsContext.restoreGraphicsState()
        context.restoreGState()
    }
}

private func drawStatusIcon(size: Int) throws -> NSBitmapImageRep {
    try bitmap(size: size) { context, side in
        let scale = side / 18
        context.clear(CGRect(x: 0, y: 0, width: side, height: side))
        context.setAllowsAntialiasing(true)
        context.setShouldAntialias(true)
        context.setStrokeColor(NSColor.black.cgColor)
        context.setFillColor(NSColor.black.cgColor)
        context.setLineCap(.round)
        context.setLineJoin(.round)
        context.setLineWidth(2.15 * scale)
        context.strokeEllipse(in: CGRect(x: 3.15 * scale, y: 3.0 * scale, width: 11.7 * scale, height: 11.7 * scale))

        // Preserve the characteristic Dotii bottom gap and two feet at menu-bar scale.
        context.saveGState()
        context.setBlendMode(.clear)
        context.fill(CGRect(x: 8.2 * scale, y: 0, width: 1.6 * scale, height: 4.3 * scale))
        context.restoreGState()
        context.fillEllipse(in: CGRect(x: 2.45 * scale, y: 1.25 * scale, width: 4.05 * scale, height: 3.7 * scale))
        context.fillEllipse(in: CGRect(x: 11.5 * scale, y: 1.25 * scale, width: 4.05 * scale, height: 3.7 * scale))
        context.fillEllipse(in: CGRect(x: 6.25 * scale, y: 8.4 * scale, width: 1.75 * scale, height: 1.75 * scale))
        context.fillEllipse(in: CGRect(x: 10.0 * scale, y: 8.4 * scale, width: 1.75 * scale, height: 1.75 * scale))
    }
}

guard let logo = NSImage(contentsOf: logoURL) else {
    FileHandle.standardError.write(Data("无法读取 \(logoURL.path)\n".utf8))
    exit(1)
}

try fileManager.createDirectory(at: appIconURL, withIntermediateDirectories: true)
try fileManager.createDirectory(at: statusIconURL, withIntermediateDirectories: true)

for size in [16, 32, 64, 128, 256, 512, 1024] {
    let icon = try drawAppIcon(logo: logo, size: size)
    try writePNG(icon, to: appIconURL.appendingPathComponent("app-icon-\(size).png"))
}

for size in [18, 36] {
    let icon = try drawStatusIcon(size: size)
    try writePNG(icon, to: statusIconURL.appendingPathComponent("status-bar-icon-\(size).png"))
}

print("已生成 Dotii macOS App 图标与菜单栏图标：\(assetCatalogURL.path)")
