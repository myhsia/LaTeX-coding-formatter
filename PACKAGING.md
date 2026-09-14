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
  由于 `.app` 在 CI 中先经过 SDK 27.0 标记补丁 (`macos_patch_sdk.sh`),
  镜像内的应用保留 Liquid Glass 外观.
* 应用未签名/未公证, 首次打开请右键选择"打开", 或执行
  `xattr -dr com.apple.quarantine /Applications/format-tex-gui.app`.
* 注: `man hdiutil` 自 macOS 26 起给出整体弃用提示, 并将各动词映射到
  `diskutil image` (`attach`→`diskutil image attach`, `create -srcfolder`→
  `diskutil image create from`, `detach`→`diskutil eject` 等), 因此仓库内
  已不再使用 `hdiutil`; 本方案只依赖 `diskutil image` (macOS 26 起可用).

### 备份位置 (backup/)

* 默认在**被扫描根目录**下建立 `backup/` 文件夹, 并按源文件相对路径镜像存放
  `.bak`: 例如 `format-tex --recursive proj` 会得到
  `proj/backup/sub/a.tex.bak`; 单独指定的文件则以该文件所在目录为根
  (`dir/backup/name.tex.bak`)。图形界面同理: "选择目录"/拖入文件夹时以该
  文件夹为根, 单独选择/拖入的文件以各自所在目录为根。
* 备份**每次运行都会刷新** (覆盖旧备份), 且仅在文件确实需要改动时创建;
  `--check`/仅检查模式不写任何文件。若备份失败 (如目录不可写), 该文件**不会**
  被修改, 其余文件继续处理。
* 备份文件后缀为 `.bak`, 因此后续扫描不会把 `backup/` 里的备份当作待处理文件;
  关闭备份可用命令行 `--no-backup` 或取消勾选 "生成备份文件 (backup/*.bak)"。

### macOS 菜单与快捷键

* 菜单栏由 Qt 的 `QMenuBar` 提供 (显示在系统菜单栏), 含三组菜单:
  **File ▸ Close (⌘W)**、**Edit** (Undo ⌘Z / Redo ⇧⌘Z / Cut ⌘X / Copy ⌘C /
  Paste ⌘V / Delete / Select All ⌘A, 按当前焦点控件自动置灰, 例如只读的
  差异面板只启用 Copy 与 Select All)、**Window ▸ Minimize (⌘M) / Zoom /
  Bring All to Front**; 应用菜单 (About/Services/Hide/**Quit ⌘Q**) 由 Qt
  自动提供. Minimize/Zoom/Bring All to Front 调用 AppKit 原生命令
  (`performMiniaturize:`/`performZoom:`/`arrangeInFront:`, 与红黄绿按钮同源),
  失败时回退到 Qt.
* 此前没有任何 Close 命令, 因此 macOS 的 ⌘W 无人处理 (只有 ⌘Q 生效);
  现在 ⌘W 与红色关闭按钮走同一条 Qt 关闭流程, 关闭最后一个窗口即退出
  (与计算器等单窗口工具一致, `quitOnLastWindowClosed` 保持默认 True).

### macOS 外观与 SDK 标记

* PyInstaller 使用预编译的 bootloader, 因此我们的可执行文件默认带的是很旧的
  SDK 标记 (实测 `sdk 12.1`), 与构建机无关. macOS 对 SDK 较旧的主可执行文件
  渲染旧式标题栏/红黄绿按钮, 所以 `macos_patch_sdk.sh` 用 `vtool` 把
  `LC_BUILD_VERSION` 的 sdk 改写为 **27.0** 并 ad-hoc 重新签名, 以启用
  Liquid Glass 外观; `minos` 保持 11.0, 旧系统仍可运行.
* 目标版本固定为 27.0, 可用 `MACOS_SDK_TARGET` 覆盖 (例如设 26.0 做对照).
  `vtool` 本身不校验版本号, 因此 CI 同时把 macOS 构建作业放在 GitHub 的
  `xcode-27` 镜像上 (macOS 27 + Xcode 27, 内含 macOS 27.0 SDK), 并在作业内
  断言 `xcrun --show-sdk-version --sdk macosx` 等于 27.0 —— 不是凭空写一个数字.
* 注: `xcode-27` 目前是 GitHub 的 preview 镜像 (可能有排队/不稳定), 待 macOS 27
  正式 GA 后可直接换回 `macos-latest`.

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
分组框下方为**带边框的文件列表** (System Settings 风格, 圆角描边 + 淡色底):
每行为系统原生文件图标 + 文件名 (完整路径见悬浮提示), 行间为细分隔线;
选中行使用**系统强调色** (取 `QPalette.Highlight`, 因此跟随用户的强调色/
增强对比度设置) 绘制**整行方角色条**并配高亮文字, 窗口失焦时按原生方式变暗;
列表支持多选 (⌘/Ctrl 点选、⇧ 连选、⌘A 全选)。
框内底部为 System Settings 风格的 **+ / −** 按钮条 (左侧, 中间竖分隔线):
**+** 直接打开文件选择对话框, **−** 从列表移除**所选**条目 (无选中时置灰);
列表为空时显示提示文字与 "选择文件/选择目录" 链接 (可拖入文件或文件夹)。
右侧为不透明的内容面板 (选项复选框、输出编码、应用按钮、差异预览与状态栏)。
选项区没有标题, 6 个复选框按面板可用宽度自适应排布: 宽度足够时为
**2 行 × 3 列**, 变窄 (或把侧栏拖宽) 时变为 **3 行 × 2 列**; 各列等宽并
均分面板宽度, 复选框在各自列内左对齐 (窗口缩放与拖动分隔条时即时重排)。

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
`% !TeX encoding` 注释时自动添加);
**点击文件列表中的条目即自动预览该文件的差异** (不再有 "预览差异" 按钮),
列表支持多选 (⌘/Ctrl 点选、⇧ 连选、⌘A 全选), 预览与 "应用格式化" 都**只作用于
所选条目** —— 选中哪些就预览/写回哪些 (默认生成备份, 见下); 选中行有高亮底色。

命令行:

```bash
format-tex file.tex                # 就地格式化 (备份到 backup/file.tex.bak)
format-tex --check file.tex        # 只报告, 不写入
format-tex --no-magic-comment file.tex     # 不添加魔法注释
format-tex --input-encoding gb2312 file.ctx   # 覆盖自动检测的输入编码
format-tex --output-encoding utf-8 file.ctx   # 输出默认同各文件检测编码
format-tex --no-punct --no-commands --loose-ranges --no-backup file.tex
format-tex --extension .ctx .      # 扫描目录下所有 *.ctx 文件
                                   # (.ctx 与 *.ctx 写法均可)
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
