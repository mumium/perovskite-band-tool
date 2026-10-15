# 测试

在项目根目录运行全部测试：

```powershell
python -m unittest discover -p "test_*.py"
```

包含能带与渐变、光学数据、SimSS 输入和真实求解、EQE、失败诊断以及 Tk 界面测试。原生 SimSS 测试需要对应平台求解器；界面测试会短暂打开窗口。

可单独运行一组，例如 `python -m unittest tests.test_performance_ui`。
