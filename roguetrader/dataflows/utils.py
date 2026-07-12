"""
数据流工具函数模块

本模块提供数据处理和输出相关的通用工具函数,包括:
- 数据保存功能
- 日期处理工具
- 类方法装饰器
"""

import os
import json
import pandas as pd
from datetime import date, timedelta, datetime
from typing import Annotated

# 定义文件路径类型注解
SavePathType = Annotated[str, "File path to save data. If None, data is not saved."]

def save_output(data: pd.DataFrame, tag: str, save_path: SavePathType = None) -> None:
    """将数据保存为CSV文件,如果提供了保存路径。"""
    if save_path:
        data.to_csv(save_path)
        print(f"{tag} saved to {save_path}")


def get_current_date():
    """返回当前日期,格式为YYYY-MM-DD字符串。"""
    return date.today().strftime("%Y-%m-%d")


def decorate_all_methods(decorator):
    """类装饰器工厂,为类的所有方法应用同一个装饰器。"""
    def class_decorator(cls):
        for attr_name, attr_value in cls.__dict__.items():
            if callable(attr_value):
                setattr(cls, attr_name, decorator(attr_value))
        return cls

    return class_decorator


def get_next_weekday(date):
    """如果输入日期是周末,返回下一个工作日;否则返回原日期。"""
    if not isinstance(date, datetime):
        date = datetime.strptime(date, "%Y-%m-%d")

    if date.weekday() >= 5:
        days_to_add = 7 - date.weekday()
        next_weekday = date + timedelta(days=days_to_add)
        return next_weekday
    else:
        return date
