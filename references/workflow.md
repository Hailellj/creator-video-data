# 项目配置、续跑和工具用法

所有命令从skill目录执行，或给scripts绝对路径。工具仅本地处理；不持有凭据，不启动Apify，不写飞书。对同一数据不要重复运行无收益校验；修正失败或数据变化后再重查相应范围。

## 创建本项目计划

由当前清单生成配置，示例中的路径和产品背景必须换成本次事实：

```json
{
  "inventory": "inventory.json",
  "tasks": ["metrics", "comments"],
  "collection_mode": "full",
  "analysis": true,
  "timezone": "UTC",
  "requested_metrics": ["views", "likes", "saves", "comments", "shares"],
  "product_context": "本项目准确的产品型号及混合波次",
  "targets": {
    "metrics": {"url": "本次原表URL", "sheet_id": "现场读取的ID"},
    "comments_template": "本次参考表URL",
    "analysis_template": "本次参考文档URL"
  }
}
```

inventory为JSON数组，至少row/source_row、source_url/url、platform；保留creator/wave/product等字段。canonical_url/resolved_url及video_id由确切来源补齐；video_id字符串。Instagram shortcode和numeric media_id不是同一种ID，不猜着替换。视频URL原始大小写保持。

```bash
python3 scripts/workflow.py prepare --config project.json --directory run-directory
python3 scripts/workflow.py resume --directory run-directory
```

prepare拒绝已有目录，生成task.json、inventory.json及按平台/任务分组。route是已验证历史建议；列出的URLs不是Actor-ready input，须用当前schema生成准确输入。真实解析、读写及配额检查后保存证据，再记录preflight。

```bash
python3 scripts/workflow.py record --directory run-directory --phase preflight --status verified --evidence preflight.json
python3 scripts/workflow.py record --directory run-directory --phase collect.metrics.TikTok --status submitted --run-id ACTUAL_RUN_ID
python3 scripts/workflow.py record --directory run-directory --phase collect.metrics.TikTok --status succeeded --evidence saved-run-and-dataset.json
```

phase以status输出为准；不要复制示例的平台名到别的平台任务。submitted/running须有真实run/session ID；完成/验证须有证据。active job不能重置pending。已有run结束后，确有依据的补抓用新run ID、`--new-attempt`和原因，历史attempt保留在events.jsonl。

collection可用partial+证据+原因表示公开缺口；analysis/publish/verify不能用partial伪装完成。失败/权限阻断写说明。未知费用留空；可获得实际费用时用`--actual-cost-usd`记录。阶段耗时是记录时点差，不代替Actor精确runTime；并行阶段总墙钟时间不能简单相加。

resume列出active_jobs及ready_phases，已有job先查状态，禁止直接重启。配置/清单被改会拒绝续跑；记录的证据被改/删除会列出integrity error，不能判complete。日志是证据索引，不独立证明云端正确。

每个阶段记录generation和当时的dependency_generations。上游失败、开始新attempt或更换证据后，依赖它的结果会传递标为stale；上游恢复不会自动恢复旧版下游验证。按ready_phases重建/重验，全部阶段有效完成且最终verify为verified才判complete。旧events日志可在重放时补推依赖版本，不要求丢弃已有任务。

## 合并当前评论与历史缓存

```bash
python3 scripts/reconcile_comments.py --current current-normalized.json --previous previous-merged.json --registry record-id-registry.json --mode full --output merged.json --report merge-report.json
```

可重复`--current`合并多个来源。默认full，旧版独有记录排除，但可复用稳定ID及满足上下文规则的翻译/编码。只有用户允许才用incremental；报告和记录保留未刷新边界。registry帮助被隐藏后又出现的评论找回已有引用ID，避免重新编号。

工具输入是规范JSON，不是未知Actor原始字段。缓存规则及冲突信息以merge-report和当前脚本输出为准，不能将旧likes/retrieved_at作为新采集数据。孤立回复和缺产品上下文要保留边界并人工核对。

## 全清单审核

```bash
python3 scripts/audit_corpus.py --inventory inventory.json --metrics metrics.json --comments merged.json --coverage coverage.json --required-metrics views likes saves comments shares --expected-videos VIDEO_COUNT --output audit.json
```

必需字段由实际表头指定；指标任务可省略comments/coverage，评论任务可省略metrics/required-metrics。通过只表示本地规范数据一致，不代表分页穷尽、翻译语义正确或当前平台访问成功。

## 飞书回读验证

```bash
python3 scripts/verify_delivery.py --expected typed-payload.json --actual comments-readback.json --actual coverage-readback.json --output delivery-audit.json
```

按实际tab数量提供回读；需要源表保留检查时提供baseline及准确允许变更范围。稀疏指标payload可只列目标cells，避免重写整个原表。详见 [feishu-delivery.md](feishu-delivery.md)。

全部工具禁止报告覆盖输入；失败退出码非零，完整报告留文件。不要仅看stdout中的样本错误或Actor的SUCCEEDED就交付。
