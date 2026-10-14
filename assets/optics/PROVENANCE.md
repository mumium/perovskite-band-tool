# 内置光学数据来源

## AM1.5G

`AM1.5G_ASTMG173.csv` 为官方 ASTM G173 参考太阳光谱原始四列表格，未修改数值。下载于 2026-10-14。

- 官方说明：https://www.nlr.gov/grid/solar-resource/spectra-am1.5
- 原始压缩包：https://www.nlr.gov/media/docs/libraries/grid/zip/astmg173.zip?sfvrsn=1ef05e45_5
- 使用 Global Tilt 列，波长单位 nm，辐照度单位 W/m²/nm；2002 个样本，280–4000 nm。
- 原始梯形积分 1000.3706555734423 W/m²。运行时按完整积分缩放到用户 Pin，不将局部波段重新归一化。

## 示例 n,k

以下 `nk_*.txt` 均原样取自 [SIMsalabim v5.36 Data](https://github.com/kostergroup/SIMsalabim/tree/v5.36/Data)，下载于 2026-10-14。上游文件使用波长 m、无量纲 n/k；程序显示和用户 CSV 使用 nm。

这些是软件演示数据，不代表当前样品，也不由输入的 Eg、χ、掺杂或材料名称推导。原始文件内容保持不变；上游许可证见 [COPYING](../../vendor/simsalabim/COPYING.txt)。MAPI 数据不能自动代表所有钙钛矿配方。

| 文件 | 覆盖范围 / nm | 上游 |
| --- | --- | --- |
| `nk_Ag.txt` | 300–800 | [原文件](https://raw.githubusercontent.com/kostergroup/SIMsalabim/v5.36/Data/nk_Ag.txt) |
| `nk_Air.txt` | 300–800 | [原文件](https://raw.githubusercontent.com/kostergroup/SIMsalabim/v5.36/Data/nk_Air.txt) |
| `nk_Au.txt` | 300–900 | [原文件](https://raw.githubusercontent.com/kostergroup/SIMsalabim/v5.36/Data/nk_Au.txt) |
| `nk_C60_1.txt` | 300–1100 | [原文件](https://raw.githubusercontent.com/kostergroup/SIMsalabim/v5.36/Data/nk_C60_1.txt) |
| `nk_ITO.txt` | 300–800 | [原文件](https://raw.githubusercontent.com/kostergroup/SIMsalabim/v5.36/Data/nk_ITO.txt) |
| `nk_MAPI.txt` | 300–900 | [原文件](https://raw.githubusercontent.com/kostergroup/SIMsalabim/v5.36/Data/nk_MAPI.txt) |
| `nk_PTAA.txt` | 300–1100 | [原文件](https://raw.githubusercontent.com/kostergroup/SIMsalabim/v5.36/Data/nk_PTAA.txt) |
| `nk_SiO2.txt` | 300–800 | [原文件](https://raw.githubusercontent.com/kostergroup/SIMsalabim/v5.36/Data/nk_SiO2.txt) |

整套内置数据的共同范围为 300–800 nm，因此默认只在此波段计算光生。扩大波段需替换所有覆盖不足的 n,k；程序禁止外推。

`example_nk.csv`、`example_spectrum.csv` 是本项目创建的文件格式示例，数值为人为设定，不是实测材料/太阳光谱。

## 原始文件 SHA256

- `AM1.5G_ASTMG173.csv`：`b48a6635ce398f7e0fa392150d68b5793ed7f2a65d9e2406bec7e6be9fb20954`
- `nk_Ag.txt`：`c4af6e7c9d2988c25cf32ce2cc7f1b2c1e4dd6347ccb0fb464a366a15acc1fbc`
- `nk_Air.txt`：`793502e5d07585ea10e5a9ff41989c840bc7ede814b4b51f04dadf83f3b7c913`
- `nk_Au.txt`：`42e154e6eb4a2a8a4f81abf3fa6956f0f98d5576cea680d70b82eb0a000c3f49`
- `nk_C60_1.txt`：`8bf21768c85598bf08c629503cd76c605882c25967b5fae9a451142bbfec4414`
- `nk_ITO.txt`：`55da4383b523c5688a6af0f2b06ec45a9f00443a4edc94c3a3af37171cbf9d1f`
- `nk_MAPI.txt`：`ee62ba48964354946e85bf177ecad7db82a0c517c6e285e8d026840abb0b3fbf`
- `nk_PTAA.txt`：`d3207d6c80f493492a3622f3fbae23b634f7d07df8d586c1b607e7887c72d1b7`
- `nk_SiO2.txt`：`faf0ae0fd474b2c730cc931bcb8d9244599dc2b5b468203e6d52ba44b7978c40`
