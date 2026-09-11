# Dotii 管理中心 macOS 宿主

该 Xcode 工程提供 macOS 13 及以上的原生菜单栏宿主。它只负责单实例、启动和监管 `DotiiBridge`、打开管理网页、查看日志、登录启动与正常退出；业务、网页和设备协议继续由共享 Python 后台提供。

## 本地构建

所有依赖、缓存和候选产物都保存在项目根目录 `.codx/`：

```bash
packaging/macos/prepare_python.sh
packaging/macos/prepare_tools.sh
packaging/macos/build_macos.sh --tools-dir .codx/macos-tools --dmg
```

完整应用由原生宿主和 `Contents/Resources/DotiiBridgeRuntime/` 中的 Python 辅助程序组成；固定版 Node.js、Codex CLI 与 LGPL 配置的 FFmpeg 位于 `Contents/Resources/tools/`。Xcode 的独立构建产物不包含这些运行组件，不能作为完整应用交付。

## 图标资源

App 图标与菜单栏模板图标位于 `DotiiManagementCenter/Assets.xcassets/`，均由现有 `bridge/web/assets/dotii.svg` 品牌标志派生。修改设计参数或品牌标志后，可在仓库根目录重新生成所有 macOS 尺寸：

```bash
packaging/macos/generate_icons.swift
```

未传入 `--sign` 时只生成 ad-hoc 签名候选包。这类包只能作为明确标注“未经 Developer ID 签名、未经苹果公证”的 GitHub Pre-release 提供；稳定版站外分发仍必须使用 Developer ID Application 签名，并通过 `--notary-profile` 完成公证与 staple。
