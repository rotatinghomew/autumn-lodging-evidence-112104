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
python3 -m unittest -v
```

资料读取命令列出各表记录数；测试检查交叉引用与格式，不生成分析结果。
