# Windows 打包

在项目根目录运行 `build_exe.bat` 生成单文件 EXE；运行 `build_exe.bat --folder` 生成文件夹版。需要先安装项目依赖及 PyInstaller。

配置文件为 `PerovskiteDeviceStudio.spec`，程序入口为根目录 `run.py`。图标、光谱、n,k、器件参数库与 SimSS 从项目根目录打包；源码模式和打包模式通过 `perovskite_studio/paths.py` 定位资源。

输出在 `dist/`，构建临时文件在 `build/`，两者不提交到 Git。已发布的 Windows 下载版见根目录 README。

EXE 与 Windows 窗口共用 `assets/logo.ico`，包含 16–256 px 共 14 档尺寸。原稿为 `assets/logo.png`，图案保持原样；其他平台窗口使用 `assets/logo-window.png`。更新原稿后，先运行 `python scripts/build_icons.py`（需要 Pillow），再重新打包。

仅在 ICO 中包含 256 px 图像并不足够：Tk 默认加载的窗口大图标为 32 px。`perovskite_studio/windows_icon.py` 在窗口映射后使用 LoadImageW 显式读取 256 px 图像，通过 WM_SETICON 设置 ICON_BIG，避免任务栏放大低分辨率窗口图标。标题栏独立设置系统尺寸的小图标，窗口销毁后释放句柄。参见 [WM_SETICON](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-seticon)。

尺寸参考 [Microsoft Windows 图标指南](https://learn.microsoft.com/en-us/windows/apps/design/style/iconography/app-icon-construction)；程序在创建窗口前设置稳定的 [AppUserModelID](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-setcurrentprocessexplicitappusermodelid)。若固定在任务栏的旧快捷方式仍显示旧图标，可取消固定，再从新版 EXE 重新固定。
