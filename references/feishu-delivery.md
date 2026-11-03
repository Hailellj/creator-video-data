# 飞书交付

优先使用支持的API/CLI批量写入；权限受阻时可通过当前已登录浏览器处理。开始时同步验证模板、读写能力及目标所有者，避免收集结束才发现创建/编辑权限问题。

先验证一个可持久化的小写入。历史原生`editCell`命令比直接模型`setValue()`可靠地触发协作保存，但属于会变动的网页内部接口。使用当前支持的浏览器工具/skill；不盲跑历史端口、tab/target ID、sheet ID或凭据。

- 文本用文本类型；长数字ID不变成浮点；以`=`开头的原文不变公式；真实计数保留数值。
- URL用原生链接保存text和target。普通URL字符串不等于链接已验证。
- Unicode载荷安全序列化；历史ASCII转义JSON解决了传输字符替换。原文还需逐项回读。
- 浏览器逐格写从小批次开始；39列Provenance最后用25行一批。按实际延迟调节，不作为所有API通用上限。
- 幂等跳过已正确单元格，保存边界。客户端超时先看实际进度，job可能已完成。
- 激活tab并等懒加载结束再读，未加载空白不视为丢失。
- 校验原生链接和准确target，写入成功后仍逐项检查链接是否存在。
- 平台NBSP→空格只对白名单记录/确切差异豁免，精确原文留raw/document。
- 复制参考表后检查遗留过滤范围、Creator/Platform逐行重复及分组合并。
- bot建文档不保证用户可编辑；历史最终用户拥有的副本才交付，核对所有者、文件夹、编辑入口及内容。

通用工具不绑定浏览器端口、target或表格ID。新项目读取inventory/config，当前API或浏览器工具负责实际操作；新目标先验证持久化小样本，再做云端回读。

## 通用回读检查器

`scripts/verify_delivery.py`已替代旧项目绑定的离线比对部分，适用两类expected：

- 表格：`{"tabs":[{"name":"Comments","headers":[...],"rows":[...],"hyperlink_columns":[13]}]}`。headers作为第0行。
- 稀疏目标：`{"tabs":[{"name":"Deliverables","cells":[{"row":2,"col":9,"value":123,"formula":null}]}]}`。row/col零基；链接目标用link定义；依脚本CLI输出给出准确结构。

actual可以重复提供单sheet回读、数组或tabs对象，须有已加载values、formulas及有关原生链接segment。匹配name/id；缺链接证据不能判链接通过，空白未加载表不能判写入通过。普通文本格出现意外URLsegment也会报错。

```bash
python3 scripts/verify_delivery.py --expected payload.json --actual tab-readback.json --baseline prewrite-readback.json --allowed-changes allowed-cells.json --normalizations explicit-normalizations.json --output final-check.json
```

expected目标格自动为允许变化范围；额外allowed-changes用`sheet,row,col,row_count?,col_count?`，只允许用户明确授权变更。baseline只能检验已捕获的非目标values/formulas/linkcells，不代表整个文件或未捕获链接全部保持。

normalizations是逐格数组，例如`{"sheet":"Comments","row":100,"col":2,"rule":"nbsp_to_space","expected":"准确原文"}`；只能转换该格准确expected中的NBSP，不能全局放宽字符检查。每个项目的例外配置保留在该项目证据中，不写进通用工具。

报告区分已检查、失败及未验证范围。工具不检查真实云端保存/新鲜度、文档、样式/过滤器/全结构或未捕获区域；这些按当前任务单独取证。必须在云端重新加载后提取actual，不能把页面本地缓存当最终回读。
