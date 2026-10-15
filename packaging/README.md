# Windows 打包

在项目根目录运行 `build_exe.bat` 生成单文件 EXE；运行 `build_exe.bat --folder` 生成文件夹版。需要先安装项目依赖及 PyInstaller。

配置文件为 `PerovskiteDeviceStudio.spec`，程序入口为根目录 `run.py`。图标、光谱、n,k、器件参数库与 SimSS 从项目根目录打包；源码模式和打包模式通过 `perovskite_studio/paths.py` 定位资源。

输出在 `dist/`，构建临时文件在 `build/`，两者不提交到 Git。已发布的 Windows 下载版见根目录 README。
