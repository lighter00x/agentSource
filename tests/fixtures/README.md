# 测试数据

| 目录 | 说明 |
|---|---|
| `jmedchem_2c00949/` | 样例论文 `10_1021_acs_jmedchem_2c00949` 的真实 MinerU 产物，复制自 `legacy_mineru_merge/test/10_1021_acs_jmedchem_2c00949/auto/`：`*_layout.json`、`*_middle.json`、`*_content_list.json`、`*_origin.pdf`、`images/`、`merged_images/`（约 19 MB）。PDF、图片和 middle.json 走 Git LFS（见 `.gitattributes`）。`tests/unit/test_real_paper.py` 和冒烟测试会优先使用它。 |
| `mini_layout/sample_layout.json` | **合成数据**。按设计文档 2.2 节的 layout.json 结构手工构造，模拟样例论文的关键特征（摘要块 `p000.b007`、p3 的 Table 1 合并组 `p003.g000`、空文本块、图注错位）。内容不是论文原文，只用于不依赖真实数据的单元测试。 |
