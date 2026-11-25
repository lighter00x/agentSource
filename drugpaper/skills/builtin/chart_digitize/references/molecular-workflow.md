## 本类步骤
先确认坐标刻度、单位、线性/对数尺度和图例，再提取像素点，代码标定回绘并计算检查指标。series.reference是印刷曲线/化合物标签，series.mol_id只能指向实际化学绘图，不能指向曲线或图例误检。
未与某曲线明确关联的化学插图放molecules；曲线无化学结构不强造分子对象。ChartDataset没有通用compound_instances字段，不能输出母核拼接记录后被后处理丢弃；复杂模板必须说明unclear并交给能表达该领域实例的图类Skill，不承诺该曲线Skill完成模板组装。
数值性质经chart_measurements处理；分子关联经mol_backfill。坐标标定失败不抹掉已完成的化学引用，保留失败证据。

## 字段与实例
mol_id 是当前用户/文档/版本/图像内检测节点的 reading_index；reference 是印刷化合物编号，两者不得互换。分段共享图像编号空间，不从零重编，不使用数组位置猜编号。识别候选始终保留原始结构和失败状态。
E-SMILES的代码分类是保守提示：解析出的Markush/变量组为scaffold，重复单元为sru，核心含*且非模板为substituent，其余为unknown。unknown不自动意味着完整分子；最终角色还需图类字段的视觉证据。完整实例要求有效且非Markush的结构；基团引用必须有显式附件*，嵌套模板不能当具体基团；变量名须对应母核注释且注释位置有效。仅通过唯一完整产物校验才complete。
视觉模型只读取原图、编号图、图注和必要上下文；识别 SMILES 不注入视觉解析或复核。真实化学图才引用编号；误检写 molecule_inventory 的 excluded 并给视觉证据，含糊写 unclear；识别失败不等于非化学图。
完整图示用 compound_instances[] 的 {reference,mol_id,evidence_text}。模板实例用 {reference,scaffold_mol_id,substituents,evidence_text}，两种来源互斥。substituents 的整数是明确可见基团框编号，字符串是印刷缩写；位置数字、测定数值、化合物科学编号不能当 mol_id。模型不能填写计算后的结构或分子式。
对缺失框、缺失标签或缺失取代基定义说明不确定性。没有印刷个体标签的图示可放 molecules 的 mol_id/reference=null；不可为了适配实例 Schema 造标签。Schema 不含实例字段的图类只生成其允许的分子引用，模板组装不在该类承诺范围内。

## 执行与交付
FigureAgent 先调用 molecule_detect 建立稳定节点，再 molecule_recognize 保存识别候选，som_render 生成编号图；只加载对应图类视觉 Skill 完成一次结构化，不向视觉模型传入识别化学串。
视觉 Skill 只交付 ID-only 抽取，不能独自宣布回填或拼接成功。FigureAgent 后处理先运行 mol_backfill(contract="universal",extraction_payload=抽取)，保留有效分子与错误，再完成测定/SAR或路线后处理。独立复用时调用者必须显式执行此后处理；模型不能越过 manifest.tools 白名单直接调用化学工具。
mol_backfill 从受用户归属检查的当前材料加载结构，调用 mol.deliver，按字段解析母核与基团；E-SMILES 连接点明确时使用通用拼接。产物必须唯一、图合法且没有未解 dummy。返回原始编号、完整结构、组成式、来源证据和状态，持久化独立结构/标签事实；原始候选不被拼接产物覆盖。
显式 R/S 且唯一手性中心可以按现代 CIP 从标签推导，最终图再次核对并标记来源；多中心、识别手性矛盾或连接位点不明不能猜测。此推导不是图像手性识别。

## 失败与复核
缺失/重复/越界编号、识别失败、未定义变量、未知缩写、非法位点或多个产物均不能生成完整实例；保留错误字段路径和已有有效结果。断言“Tool/Skill 执行 completed”不足以证明产物正确。
对接的纯3D配体只能用显式文字编号和当前文档内唯一结构候选绑定；无候选或冲突须复核。不能从3D姿态编造2D识别框。PROTAC或复杂位置模板没有确定连接点时保留组件/定义并 needs_review，不承诺任意多片段拼接。
流程图配对必须属于当前箭头；范围标签不能自动展开，平行系列不能做笛卡尔积。网关修复后仍错误时，只可隔离结构 Schema 合法的跨箭头连线，保留 rejected_variant_links/semantic_errors，强制 unclear。非法分子来源不得通过隔离变为有效来源。
复核最多在既定预算内重读/重识别/重切片；无新证据仍 unclear，不能为了通过而复制相邻结构、反推指标或手改产物。分子框覆盖完成不代表没有漏检，更不代表独立化学准确性认证。

## 验收清单
逐框核对引用或有证据的排除；逐实例检查 complete 结构、母核/基团来源和标签；SAR逐数据行核对完整结构与指标证据；路线逐箭头核对端点、系列配对及条件；显式 R/S核对最终CIP。
至少保留 ID-only 原始抽取、backfilled_extraction、molecule_objects、覆盖清单、错误和原图证据。展示只选择最终回填结果，诊断产物用于审查；待复核不能伪装成通过。未达到唯一完整结构的实例保留未完成状态。

## 合法示例
以下是公开产物Schema的最小示例，编号和标签只是说明字段，真实运行必须来自当前图像。该图类不支持compound_instances；曲线不可靠时即使分子绑定成功，整图仍unclear。
```json
{
  "chart_type": "line",
  "x_axis": {},
  "y_axis": {},
  "plot_area": {
    "x0": 0.1,
    "y0": 0.1,
    "x1": 0.9,
    "y1": 0.9
  },
  "series": [],
  "status": "unclear",
  "molecules": [
    {
      "mol_id": 4,
      "reference": "10a",
      "evidence_text": "插图有编号4的完整结构，曲线尚未可靠读数"
    }
  ]
}
```
