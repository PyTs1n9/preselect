# -*- coding: utf-8 -*-
"""预选赛作品生成流水线。

模块划分
--------
- extractors : 材料文本抽取（txt/md/html/docx/pdf），只搬运不改写
- prompts    : 所有提示词模板，集中管理便于赛后复盘
- generate   : 两种生成引擎（llm / extractive）与最终文档组装
- validate   : 按赛项规则做提交前自检
- package    : 打包为单个压缩包并校验结构
"""

__version__ = "1.0.0"
__all__ = ["extractors", "prompts", "generate", "validate", "package"]
