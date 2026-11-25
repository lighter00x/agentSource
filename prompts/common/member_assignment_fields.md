## 编号成员赋值的字段语义

先辨认表头/标注是在回答“哪个成员”“是什么基团/原子”还是“位于哪里”，再填写字段。表格的一列不必对应一个substituents键；位置列、活性列不能变成基团赋值。

位置检查按来源是否涉及位置启用，不是所有文献的固定必填项。来源明确给出位号、位置列或基团移位时，核对并保留实际信息；没有单独位置记录且图示对应没有歧义时，position_definitions可省略或为{}，不得要求补造位号、另建位置列、为了填字段而额外检索。如果来源确有位置歧义且影响成员身份，只报告该具体成员/基团的缺口，不扩展为所有成员都缺少位置。图上局部赋值键的选择与文献位号是两种信息：存在attachments不代表必须有位置字段，位置为空也不免除已有赋值的自洽检查。

| 字段 | 用途 | 不得混入的内容 |
| --- | --- | --- |
| reference | 论文中的单个成员编号或文字指代 | 检测框ID、表格行序号、位置编号 |
| mol_id / scaffold_mol_id | 当前图的完整分子/通式母核检测ID，二者按Schema互斥 | 论文编号、参考图的检测ID、化学位号 |
| substituents | 键是图示变量或完整局部基团名，值回答“该成员是什么基团/原子”，如XCHYZ=OCH2F、R=COMe、Y=N；内部变量可作解释保留 | 仅表示位置的4、2′、para，测量值，未确定的候选列表 |
| position_definitions | 键是来源中的基团/位点名，值回答“位于哪里”，如CHO=4、XCHYZ=2′；允许记录母核上已有固定基团的移位 | 基团身份的替代记录、活性数值、整句解释；不要求其每个键同时出现在substituents中 |
| attachments | 从substituents选择后续要处理的完整局部赋值键；只存键，不存基团值、位置或表格列名 | 仅因出现位置列就新增固定基团、把内部解释变量当额外连接点 |
| evidence_text | 记录来源图表/成员行/表头、实际可见值及不确定性，供复核追溯 | 无依据的修正、把审查意见冒充来源原文 |

substituents字符串是有来源的局部基团/原子简称，不是完整分子结构。输出Schema允许整数时，整数只表示当前图实际检测到的基团ID；不能将位置数字由字符串改成整数来绕过语义限制。record_reference_assignments只接受字符串赋值。

位置与基团合印时，忠实保留其身份信息：来源明确写X=3′-Cl，可以保留这一带位号基团原文；位置独立记录时position_definitions.X可写3′，无需重复整句。来源独立列出“CHO position=4”时，4只有位置含义，绝不能写substituents.CHO="4"。不同位号体系的撇号必须保留，2与2′不互换。

例：某表成员1行给出CHO position=4、XCHYZ position=4′、X=O、Y,Z=H,F；当前图母核已有CHO和XCHYZ局部表达式。可记录为（ID及名称仅为本例）：

```json
{
  "reference": "1",
  "scaffold_mol_id": 1,
  "substituents": {"XCHYZ": "OCH2F"},
  "position_definitions": {"CHO": "4", "XCHYZ": "4′"},
  "attachments": ["XCHYZ"],
  "evidence_text": "来源表成员1行：CHO position=4；XCHYZ position=4′；X=O，Y,Z=H,F。"
}
```

错误写法：substituents={"CHO":"4","XCHYZ":"OCH2F"}、attachments=["CHO","XCHYZ"]。CHO的位置变化必须保留，但位置列不提供新的CHO取代赋值，也不把母核已有CHO变成新增连接项。只有来源明确给出该基团本身的替换/赋值时，才另行判断其substituents与attachments。

提交或审查时分别核对基团身份与位置信息。纠错应保留原来正确的字段；位置缺失只补位置，不能靠复制位置到substituents或attachments凑齐字段。结构化与来源审查只核对图像语义及字段自洽，不比较识别SMILES标签或推测原子映射；实际可拼接性由后续回填/拼接验证。

## LLM归一化与有限一致性检查

前期解释与合并由当前Agent/视觉LLM负责，无需再调用一个模型判断是否改交函数。按当前文献实际表达，将成员内部变量合并成完整局部基团，供后续固定拼接函数使用；保留内部变量、原图表达式、位置及来源，不要求固定函数覆盖所有论文写法，不输出完整分子或自写SMILES。

合并时逐项保留原表达式的显式原子与氢。例如XCHYZ已有一个H，X=O、Y=H、Z=F时完整局部基团为OCH2F；Y=F、Z=F时为OCHF2。OCHF、OCF2分别少了显式H，不与原赋值等价。整体基团放substituents.XCHYZ，内部X/Y/Z可同时保留，attachments仅选择完整表达式键，不把内部变量当成三个独立连接项。示例只说明原则，变量名、基团和拓扑以本图为准。

宿主normalization_checks是有界辅助检查，仅核对支持的扁平局部表达式及显式计数，不计算完整分子、不验证连接拓扑。consistent只说明这一项计数未发现矛盾，不代表来源或拼接已通过；not_checked表示缺少内部赋值或表示超出检查范围，继续由LLM按来源核对，不因此拒绝真实复杂表达式，也不能改写证据迎合检查器。不存在明确矛盾时不强制补造内部变量。

member_normalization_conflict反馈会列出成员、表达式、原始赋值、当前整体值及具体矛盾。根据原图修正LLM输出后重交原计划批次，保留其中正确成员；不要只提交错误行而遗漏同批其他成员。已登记记录有误时，使用record_reference_assignments的supersedes与correction_reason做可追溯修订，再试拼；不要在try_assembly反复提交与来源登记冲突的值。
