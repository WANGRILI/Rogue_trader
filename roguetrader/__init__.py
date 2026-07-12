"""
RogueTrader包初始化模块

此模块在包导入时自动执行，用于设置全局环境。

功能:
- 设置Python UTF-8编码支持，确保正确处理中文字符
"""

import os

# 设置默认Python UTF-8编码
# 解决某些系统上默认编码不是UTF-8导致的编码问题
os.environ.setdefault("PYTHONUTF8", "1")
