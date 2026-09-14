# 打包与分发说明 / Packaging & Distribution

本工具包含命令行版 (`format_tex.py`) 和图形界面版 (`format_tex_gui.py`),
均已打包为免安装 Python 的单文件可执行程序。

## 本机已生成的文件 (macOS, Apple Silicon arm64)

```
dist/
  format-tex-gui-macos-arm64      图形界面版 (命令行直接运行)
  format-tex-gui.app              图形界面版 (双击运行)
  format-tex-macos-arm64          命令行版
```

运行方式:

```bash
chmod +x dist/format-tex-gui-macos-arm64
./dist/format-tex-gui-macos-arm64
# 或双击 dist/format-tex-gui.app
```

从网络下载的用户如被 macOS Gatekeeper 拦截, 请右键点击程序 → "打开"。

注意: 本机生成的二进制文件仅支持 Apple Silicon (M 系列) 芯片的 Mac;
Intel 芯片的 Mac 及 Windows/Linux 用户请使用下面的 CI 自动构建产物。

## 本机自行构建 (macOS)

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name format-tex-gui \
  --distpath dist --workpath pyibuild --specpath pyibuild format_tex_gui.py
pyinstaller --onefile --name format-tex \
  --distpath dist --workpath pyibuild --specpath pyibuild format_tex.py
```

## Windows / Linux 二进制 (GitHub Actions 自动构建)

PyInstaller 不支持跨平台编译, Windows/Linux 版本通过 CI 在对应系统上构建:

1. 将本目录 (即 `build/`) 作为仓库根目录初始化并推送到 GitHub
   (确保 `.github/workflows/build.yml` 位于仓库根目录下):

   ```bash
   git init && git add . && git commit -m "TeX CJK formatter"
   git remote add origin git@github.com:<用户名>/<仓库名>.git
   git push -u origin main
   ```

2. 在 GitHub 仓库页面打开 **Actions → build → Run workflow** 手动触发,
   或推送一个 `v*` 标签 (`git tag v1.0 && git push --tags`) 自动触发。

3. 构建完成后在 workflow 运行页面的 **Artifacts** 中下载:

   | 产物 | 内容 |
   |---|---|
   | `format-tex-windows-x86_64` | `format-tex-gui-windows-x86_64.exe`, `format-tex-windows-x86_64.exe` |
   | `format-tex-windows-arm64`  | Windows on ARM (WOA, 如 Surface) 原生版本; **不兼容 x64** |
   | `windows-msi`               | `format-tex-windows-x86_64.msi`, `format-tex-windows-arm64.msi` |
   | `format-tex-macos-arm64`    | 图形界面版、命令行版 (arm64) 以及 `.dmg` 安装镜像 (`format-tex-gui-macos-arm64.dmg`) |
   | `format-tex-linux-x86_64`   | 图形界面版与命令行版 (x86_64) |

   每个平台的二进制在 CI 上均通过冒烟测试 (中文 English 中文 → 中文 English 中文)。

### Windows 安装包 (MSI)

* 两个 MSI 分别面向 x64 与 Windows on ARM (arm64); arm64 版本在 Surface 等
  WOA 设备上原生运行, 性能更好. CI 使用 `windows-11-arm` 运行器构建 arm64
  二进制 (**该运行器仅对公开仓库开放**), MSI 则在 x64 运行器上由 WiX 打包
  (两种架构的负载分别下载后在同一个任务中打包).
* MSI 为 **per-user 安装** (`%LOCALAPPDATA%\Programs\LaTeX Coding Style
  Formatter`), 无需管理员权限/UAC, 并创建开始菜单快捷方式; 内含图形界面版的
  onedir 目录 (启动更快) 与命令行版单文件 exe.
* MSI 与各 exe 均**未签名**, 首次运行/安装时 Windows SmartScreen 可能提示
  风险 (选择"更多信息 → 仍要运行"即可); 如需消除提示请配置代码签名证书.
* MSI 使用 WiX Toolset v7 构建. WiX 自 v6/v7 起要求接受 OSMF (Open Source
  Maintenance Fee) EULA, CI 中通过 `-acceptEula wix7` 显式接受 (该接受标记
  与主版本号绑定); OSMF 对**有营收**的使用者另有付费义务.
  CI 将 WiX 固定为 `7.0.0`: 升级到 v8 时需同时把标记改为 `wix8`.
* 版本号取自推送的 `v*` 标签 (例如 `v1.0` → `1.0.0`), 手动触发时默认 `1.0.0`.

### macOS 安装镜像 (DMG)

* `format-tex-gui-macos-arm64.dmg` 内含 `format-tex-gui.app` (拖入
  Applications 即可安装)、指向 `/Applications` 的替身, 以及 `bin/format-tex`
  命令行版 (可复制到 `/usr/local/bin` 等目录使用).
* 镜像仅使用 Apple 当前推荐的工具生成与校验, 不使用任何已弃用命令:
  `diskutil image create from --format UDZO --volumeName ...` 创建;
  创建前先在暂存目录里校验 payload (应用可执行文件、`bin/format-tex` 可执行
  并实际运行 `--help`、`Applications` 替身指向 `/Applications`), 创建后用
  `diskutil image info` (含 `--plist`) 校验镜像格式与大小, 全程不挂载.
  由于 `.app` 在 CI 中先经过 SDK 26 标记补丁 (`macos_patch_sdk.sh`),
  镜像内的应用保留 Liquid Glass 外观.
* 应用未签名/未公证, 首次打开请右键选择"打开", 或执行
  `xattr -dr com.apple.quarantine /Applications/format-tex-gui.app`.
* 注: `man hdiutil` 自 macOS 26 起给出整体弃用提示, 并将各动词映射到
  `diskutil image` (`attach`→`diskutil image attach`, `create -srcfolder`→
  `diskutil image create from`, `detach`→`diskutil eject` 等), 因此仓库内
  已不再使用 `hdiutil`; 本方案只依赖 `diskutil image` (macOS 26 起可用).

## 使用方法

界面为类似 Finder 的左右布局: 左侧为文件列表栏, 其毛玻璃层为**整窗高度
的一整块** (从窗口顶端直到底部, 与标题栏左侧连成一体, 中间没有任何分隔线);
左右之间没有竖线, 二者仅由"毛玻璃层 vs 不透明内容"区分; 分隔位置附近保留
约 6 pt 的透明拖动区域 (悬停显示缩放光标) 用于调整宽度;
窗口标题左对齐于侧栏边界右侧 12 pt 处 (随拖动/缩放自动跟随)。

材质与模糊方式按 Finder 的做法区分 (见 Apple 文档 NSVisualEffectView
BlendingMode): 标题栏右段 (侧栏右侧) 使用工具栏材质 (默认 headerView) 且
blendingMode = withinWindow, 即只模糊窗口内部内容 —— 该材质层位于 Qt 视图
**之上** (其下方为不透明内容面板, 若放在 Qt 之下会被面板完全遮住而看不到任何
材质), 因此呈现为"其后有不透明层"的磨砂效果; 该层为点击穿透
(hitTest: 返回 nil), 不影响 Qt 控件交互. 侧栏使用 sidebar 材质且
blendingMode = behindWindow, 位于 Qt 视图**之下** —— Qt 侧栏控件透明, 因此
材质可见; 该层覆盖侧栏整高 (含标题栏左侧区域), 红黄绿按钮与侧栏处于同一模糊
层上, 与侧栏之间无接缝.

窗口标题由本程序自行绘制 (`nswin.titleVisibility = hidden` + 一个置于毛玻璃层
之上的 NSTextField): AppKit 会把**非 main 窗口**的标题画成未强调的灰色, 而 Qt
窗口 `canBecomeMainWindow = NO` 永远无法成为 main, 因此原生标题始终偏灰
(实测 #9a9b9c, 而 Finder/Notes 为 #e8e8e9/#ededed). 自绘标题使用系统 primary
标签色与 semibold 字重 (macOS 26 工具栏标题风格), 并在窗口失去焦点时切换为
secondary 标签色, 复现原生的"非活动变暗"行为 (实测活动态 #dddddd).

内容面板 (右侧) 使用系统原生窗口背景色 (NSColor.windowBackgroundColor),
而不是 Qt 调色板的近似值; 标题栏右段与下方内容之间**没有分隔线**, 仅以材质与
底色的差异区分 (与 Finder 一致). 可用环境变量 FORMAT_TEX_BAND_MATERIAL 覆盖
色带材质 (sidebar|headerView|titlebar|underWindowBackground) 以便对比;
设置 FORMAT_TEX_DEBUG=1 会为原生材质层 (标题带=红、侧栏=蓝) 与 Qt 内容面板
绘制 1 px 轮廓, 便于用截图核对各区域边界.

侧栏顶部为 macOS 设置风格的分组框 (圆角浅灰底, 两行之间为细分隔线):
第一行左侧 "扩展名"、右侧为下拉按钮 (宽度自动适配其弹出菜单, 使弹出菜单与按钮
等宽对齐、高亮行与按钮完全重合; 同时保证文本与箭头不被裁切), 第二行
左侧 "含子目录"、右侧为原生开关 (NSSwitch, 真·AppKit 控件叠加于 Qt
视图之上, 无法创建时回退为复选框);
分组框下方为 "清空列表" 按钮与文件列表; 右侧为不透明的内容面板
(选项、输出编码、预览/应用按钮、差异预览与状态栏)。

GUI: 点击文件列表区的 "选择文件"（可多选）或 "选择目录" 链接来选择;
或直接把一个/多个文件或文件夹从访达 (Finder) 拖放到文件列表区
(松开后: 文件直接加入列表, 文件夹按当前 "扩展名" 和 "含子目录"
设置扫描后加入列表);
"选择目录" 会扫描所选目录下匹配 "扩展名" 输入框 (默认 .tex,
可勾选 "含子目录" 递归扫描) 的所有文件并加入列表;
输入编码自动检测 (chardet + 内置启发式回退), 每个文件独立检测;
输出编码默认 "同输入" (即与各文件检测到的编码一致), 可在 "输出编码"
输入框选择任意编码 (如 utf-8) 统一转换。
按需勾选选项 ("添加编码魔法注释" 默认勾选, 会在文件缺失
`% !TeX encoding` 注释时自动添加),
"预览差异" 查看将做的修改, "应用格式化" 写回文件 (默认生成 `.bak` 备份)。

命令行:

```bash
format-tex file.tex                # 就地格式化 (生成 .bak 备份)
format-tex --check file.tex        # 只报告, 不写入
format-tex --no-magic-comment file.tex     # 不添加魔法注释
format-tex --input-encoding gb2312 file.ctx   # 覆盖自动检测的输入编码
format-tex --output-encoding utf-8 file.ctx   # 输出默认同各文件检测编码
format-tex --no-punct --no-commands --loose-ranges --no-backup file.tex
format-tex --extension .ctx .      # 扫描目录下所有 *.ctx 文件
format-tex --extension .tex --recursive .   # 递归扫描 (含子目录)
```

注意: 从源码运行需要 `pip install chardet PySide6-Essentials`
(macOS 另需 `pyobjc-framework-Cocoa` 用于 Liquid Glass 窗口效果);
打包的二进制文件已内置全部依赖。

GUI 基于 Qt (PySide6)。macOS 上自绘 52 pt 标题栏色带
(NSVisualEffectView, sidebar 模糊材质), 原生红黄绿按钮与窗口标题
垂直居中于色带内, 红黄绿按钮左边距采用系统统一工具栏的 19 pt 原生间距
(可用环境变量 FORMAT_TEX_LIGHTS_INSET 微调), 窗口标题左对齐于
红黄绿按钮右侧 8 pt 处 (可用 FORMAT_TEX_TITLE_GAP 微调); AppKit 布局会重置,
已在窗口缩放/激活/全屏后自动重新校正; 色带与内容区之间为 1 pt 分隔线,
内容区为不透明窗口底色; 全屏时自动隐藏色带。
Windows 11 使用 Mica (旧版回退到 Acrylic/纯色),
Linux 使用系统 Qt 主题并跟随深浅色模式。

macOS 上 "扩展名" 与 "输出编码" 两个下拉框为原生弹出按钮
(上下箭头), 点击后弹出系统菜单 (macOS 26+ 为液态玻璃材质),
当前项左侧带对勾; 弹出菜单宽度与按钮完全一致, 并将当前项对齐覆盖在
按钮上 (原生弹出按钮行为); 需要自定义值时选择菜单中的 "其它" 输入即可。

规则详见 `format_tex.py` 模块文档字符串。
