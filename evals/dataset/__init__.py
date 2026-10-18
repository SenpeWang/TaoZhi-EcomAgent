"""独立评测数据包：规格（spec/）→ 生成（generate.py）→ 灌库（seed.py）→ 校验（schema.py）。

设计目标：评测集不依赖任何私有语料与手工夹具状态，全部由进 git 的规格重建。
- spec/       唯一事实源：组织身份、权限夹具、检索语料、用例模板
- generate.py 确定性渲染用例与语料文档，产出带 manifest 的数据集
- seed.py     幂等灌库（--verify 校验夹具状态，--clean 清除）
- schema.py   用例契约校验，CI 强制执行
"""
__version__ = "1.0.0"
