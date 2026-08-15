// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "LCP",
    platforms: [.macOS(.v13), .iOS(.v16)],
    products: [.library(name: "LCP", targets: ["LCP"])],
    dependencies: [
        .package(url: "https://github.com/ajevans99/swift-json-schema.git", from: "0.13.1")
    ],
    targets: [
        .target(name: "LCP", dependencies: [.product(name: "JSONSchema", package: "swift-json-schema")], resources: [.process("Resources")]),
        .testTarget(name: "LCPTests", dependencies: ["LCP"])
    ]
)
