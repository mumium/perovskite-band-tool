# 维护脚本

`build_icons.py` 将原有 `assets/logo.png` 编码为包含 14 档尺寸的 `assets/logo.ico` 与 256 px 的 `assets/logo-window.png`，不修改原稿。需要 Pillow，运行 `python scripts/build_icons.py`；日常启动程序不需要 Pillow。

`prepare_simss_source.py` 从上游 SIMsalabim v5.36 源码生成本项目分发的求解器源码及网格修正。

在项目根目录运行：

```powershell
python scripts/prepare_simss_source.py C:/path/to/SIMsalabim-5.36
```

输出在 `vendor/simsalabim/source/`。日常使用桌面工具不需要运行此脚本；不会重新编译或替换现有 SimSS 可执行文件。
