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
   | `format-tex-macos-arm64`    | 图形界面版与命令行版 (arm64) |
   | `format-tex-linux-x86_64`   | 图形界面版与命令行版 (x86_64) |

   每个平台的二进制在 CI 上均通过冒烟测试 (中文 English 中文 → 中文 English 中文)。

## 使用方法

界面为类似 Finder 的左右布局: 左侧为文件列表栏 (与标题栏同层的
毛玻璃材质, 可拖动中间分隔条调整宽度)。侧栏顶部为 macOS 设置风格的
分组框 (圆角浅灰底, 两行之间为细分隔线): 第一行左侧 "扩展名"、
右侧为下拉按钮 (宽度自动适配其弹出菜单, 使弹出菜单与按钮等宽对齐、
高亮行与按钮完全重合; 同时保证文本与箭头不被裁切), 第二行左侧
"含子目录"、右侧为原生开关 (NSSwitch, 真·AppKit 控件叠加于 Qt 视图
之上, 无法创建时回退为复选框);
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
