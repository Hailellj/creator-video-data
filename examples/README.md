# 离线示例

这里有3条虚构视频和5条虚构评论。视频编号、创作者、评论内容及数值都是为演示编写的，没有查询任何平台。示例链接里的内容编号不是合作视频，请不要把这些数据当成真实结果。

在仓库根目录运行以下命令即可体验数据检查：

```bash
python3 scripts/audit_corpus.py --inventory examples/inventory.json --metrics examples/metrics.json --comments examples/comments.json --coverage examples/coverage.json --required-metrics views likes saves comments shares --expected-videos 3
```

比较预期表格和示例回读：

```bash
python3 scripts/verify_delivery.py --expected examples/payload.json --actual examples/readback.json
```

生成本地任务清单：

```bash
python3 scripts/workflow.py prepare --config examples/project.json --directory .demo-run
python3 scripts/workflow.py resume --directory .demo-run
```

这些命令只处理本地文件，不抓视频、不花Apify额度，也不写入飞书。`.demo-run`是生成的练习目录；已有同名目录时，用resume查看进度，或为下一次练习选择新目录。

`metrics.json`中的数字0表示确认零，null表示不可得。`comments.json`保留原文、译文、评论编号和父评论编号。`payload.json`是准备写入的表格；`readback.json`是合成的读取结果，用来演示链接和数据类型检查。
