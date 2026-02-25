# 回归测试

## 运行测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## 测试范围

- **test_parser.py**：简谱解析（parse）
- **test_validator.py**：验证与诊断
- **test_scheduler.py**：事件调度
- **test_preprocessor.py**：import 展开
- **test_regression.py**：workspaces 中所有 .choir 文件的解析/验证/调度

推送或 PR 到 main/dev 时，GitHub Actions 会自动运行测试。
