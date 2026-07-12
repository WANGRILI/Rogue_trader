"""
CLI入口模块 - 包初始化文件

此文件使 cli 目录可以作为Python包运行。
当执行 `python -m cli` 时，此文件会被调用。

功能:
    - 导入主应用实例
    - 启动Typer CLI应用
"""

from .main import app

app()
