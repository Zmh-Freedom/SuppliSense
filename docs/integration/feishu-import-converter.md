# 供应商注册 Excel 转飞书三表 CSV

脚本：`backend/scripts/convert_supplier_workbook.py`

## 用法

在项目根目录执行：

```bash
python backend/scripts/convert_supplier_workbook.py \
  "/path/to/供应商注册导入验证数据-合并版.xlsx" \
  --output-dir outputs/feishu_supplier_import \
  --default-category "汽车零部件"
```

生成文件：

```text
outputs/feishu_supplier_import/
├── 供应商主数据.csv
├── 供应商供货能力.csv
├── 供应商联系人.csv
└── conversion_report.json
```

生成的三个 CSV 可以分别导入飞书对应的数据表。CSV 使用 UTF-8 BOM，便于中文字段正确识别。

## 默认规则

- 供应商代码来自 `1-基础信息` 的“供货商代码”；供应商全称来自“供应商全称”。
- 供应商状态默认是“正常”，可以通过 `--default-status` 调整。
- 原表没有标准品类字段，供货能力的“品类”默认是“待分类”；建议根据业务品类传入 `--default-category` 后再导入。
- 能力状态默认是“待验证”，不会把原表数据自动宣称为已验证。
- 数据更新时间默认使用源 Excel 文件修改时间；如果有准确的业务更新时间，应通过 `--updated-at` 传入。
- 联系人类型根据职务关键词推断；联系人验证状态默认是“未知”。
- 资质证书会按供应商代码汇总到供货能力的“相关资质概况”。
- `5-业务能力`、`6-主要客户` 和 `7-关联企业` 在当前三表契约中没有直接对应字段，会在转换报告中列为未映射来源，不会被删除或伪装写入其他字段。
- 缺少主数据供应商代码的子表记录会跳过并写入报告；重复主数据代码会合并非空字段并告警。

脚本只读取源 Excel，不会修改源文件，也不会调用飞书 API。
