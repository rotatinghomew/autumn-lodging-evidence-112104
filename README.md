# 面向旅宿采购的差评证据与试接资格

采购以房型服务、真实住宿和回访记录评估续接及试接。货源/现场时段用各自的稳定编号。

## 资料交换

`data/samples.json` 是合成样册。`fields` 给出有序列名，`rows` 按该顺序记录数据，`meaning` 标明单位或来源区别。没有填写的值用 `null` 表示。

- `properties`：专用条件独立于普通房。 列：`hotel`, `rooms`, `special`。
- `stays`：房型为真实入住房型；promise 为合同服务。 列：`stay`, `hotel`, `room`, `channel`, `window`, `party`, `promise`。
- `feedback`：无邀评或无答复保留为空。 列：`feedback`, `stay_candidates`, `invited`, `score`, `concern`, `seq`。
- `appeals`：记录异议及实际适用订单，不覆盖原回访。 列：`feedback`, `verified_stay`, `issue`, `finding`, `seq`。
- `visits`：一次到访可见证的实际房间条件。 列：`slot`, `hotel`, `witness_options`, `cost`, `capacity`。
- `intake`：state 和 alternatives 为已受理状态及合同认可房源。 列：`group`, `hotel`, `need`, `rooms`, `state`, `alternatives`。
- `limits`：下期审批采用的现场抽查经费、试接量、专用保留和最低可比观察。 列：`visit_budget`, `trial_rooms`, `protected_rooms`, `min_comparable`。
- `room_inventory`：可执行房量按实际服务能力统计。 列：`hotel`, `room`, `window`, `feature`, `rooms`。

## 检视

使用 Python 3.11，资料读取不依赖第三方包。

```sh
python3 source_book.py data/samples.json
python3 analysis.py data/samples.json
python3 -m unittest discover -s tests -v
```

资料读取命令列出各表记录数；分析命令输出完整经营分析报告。
结构测试检查交叉引用与格式，规则测试检查分析口径，均不依赖第三方包。

## 分析口径（analysis.py）

三类结论分开列示，互不抵消：

1. **严重承诺违约**：有效在住订单承诺了专用服务、当时存在可出售条款与可执行房量，
   且异议核实最新裁定为「核实未交付」。一条即成立，不能被评分均值或其他好评解除；
   「待复核」等中间态不进终态，「已提供但登记遗漏」推翻原抱怨但不改写原评分。
2. **体验评分**：只统计在可匹配入住条件（旅店/房型/时段/客群）下的已观察回访，
   大团与散客分别校正；已核实为违约或被推翻的抱怨不进体验差距。
3. **未获观察**：未邀评、邀评未回复、未关联订单的匿名评论一律不计满意，保留为未知。

匿名评论只能凭异议核实的 `verified_stay` 关联订单；没有核实记录时只进核查优先级
（专用服务抱怨优先），不按店型差评数量硬配。

报告主要段落：

- `评论证据对应` / `匿名核查优先级` / `严重承诺违约` / `被推翻的抱怨` / `待核实抱怨`
- `可比体验分段`（含支撑度、均分、未观察清单）/ `客群校正汇总` / `邀评覆盖`
- `商户下周去向`：信息不足、证实履约问题、同类客群校正后的体验差距三类分开
- `缺证条件` / `抽查计划`：一次到访见证多条件只计一次费用，受 `capacity` 与经费约束，
  同楼未列入 `witness_options` 的条件不算覆盖
- `试接批次`：必须补到缺证房型或时段；已接专用客户不进试接名单；
  每条批次可反查补哪段证据、配对抽查与未处理义务
- `接团处置`：已接团先看合约认可替代，不可行则转人工协商并冻结相应床位
  （不得悄悄重新上架）；回访改变风险后，待接试团、普通续接、专用承诺分别重判
