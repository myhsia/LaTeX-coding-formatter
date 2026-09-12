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

GUI: 点击 "选择文件…" 添加 TeX 文件, 或点击 "扫描目录…" 扫描某个目录
(默认为列表中第一个文件所在目录) 下匹配 "扩展名" 输入框 (默认 .tex,
可勾选 "含子目录" 递归扫描) 的所有文件并加入列表;
按需勾选选项, "预览差异" 查看将做的修改, "应用格式化" 写回文件
(默认生成 `.bak` 备份)。
"文件编码" 栏可选择输入/输出文件编码: 输入默认 utf-8, 输出默认 "同输入"
(即与输入编码一致), 内置 gb18030、gbk、gb2312、big5、utf-16、latin-1
等预设, 也可输入任意编码名。

命令行:

```bash
format-tex file.tex                # 就地格式化 (生成 .bak 备份)
format-tex --check file.tex        # 只报告, 不写入
format-tex --no-punct --no-commands --loose-ranges --no-backup file.tex
format-tex --input-encoding gb2312 file.ctx        # 输出默认同输入编码
format-tex --input-encoding gb2312 --output-encoding utf-8 file.ctx
format-tex --extension .ctx .      # 扫描目录下所有 *.ctx 文件
format-tex --extension .tex --recursive .   # 递归扫描 (含子目录)
```

规则详见 `format_tex.py` 模块文档字符串。
