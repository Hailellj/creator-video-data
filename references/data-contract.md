# 规范数据

指标 JSON数组：`row/platform/source_url/resolved_url/retrieved_at/status/metrics/notes`，及可获得的Actor/run ID。metrics按用户表头映射，常用views/likes/saves/comments/shares；未知为null，确认零为数字0；平台负哨兵先规范化。Views不等于独立人数。

评论 JSON数组：

- `record_id/unique_key`：稳定引用 ID、平台+视频+原 comment ID唯一键，增量不重排旧ID。
- `source_row/platform/source_url/canonical_url/video_id`：准确原清单对照。
- `comment_id/parent_id/author/author_id/is_creator`：ID为字符串；无父ID空串，缺父评记录原因。
- `text_original/text_en/text_zh/media_only/original_display`：原文不改写；媒体说明有源依据。
- `likes`：已测数字或null，不默认0。
- `created_at/time_precision/retrieved_at`：不可得时间、相对时间均标精度。
- `feedback_scope/is_direct_current_phone/sentiment/intent/topics`：统一标签、代码本版本和歧义说明。
- `comment_url/comment_url_provenance/retrieval_method/run_id/dataset_id/raw_source`：保留来源；构造链接与源返回链接区分。

Coverage每个当前发布视频恰好一行，即使为零或不可得：`source_row/platform/source_url/unique_records/top_level/replies/retrieved_at/method/raw_status/notes`，已知计数及各自时间。状态区分公开核实零、可得分页结束、计数/回复缺口、原内容不可得、权限阻断、未完成。不要把异时差额直接解释为删评数量。

可由source/canonical URL严格对齐的历史URL型unique_key可以显式迁移，保留legacy key及已有record_id；不能凭字符串相似度猜映射。跨快照引用ID有独立registry，不以当前排序重编号。

完整刷新只使用本轮实际取得的记录；被隐藏或接口没返回的旧记录留历史文件及registry，不混入当前全集。增量中携带旧记录必须保留旧retrieved_at并明确未刷新，新增/编辑/likes变化分别识别；旧记录缺失不能直接断言删除。

合并器的记录标记为`_reconcile.snapshot_status`：`current`表示当前输入取得，`carried_previous_unrefreshed`表示增量携带历史记录。它是输入快照归属，不独立证明采集时间真实。`legacy_unique_key`保留迁移前键。

翻译缓存比较原文、parent_id和递归父链原文，以及wave/product_context/video_context/product_id/model/creator/source_url/canonical_url/author/author_id/is_creator/media_only/original_display/media_metadata/attachments。translation_version须一致；语义额外要求非空且一致codebook_version。缺父链或背景变化不复用旧版语义，已有当前译文仍需人工/语义验证。

`audit_corpus.py`检查重复ID、ID类型、父子/自指/循环、翻译是否存在、媒体标记、点赞类型、逐视频Coverage主评及回复数量、清单/URL/已知视频ID、必需指标缺列、非负数/null。缺父评、缺公开计数和分页差额可作为明确边界，不能偷偷填0。它不证明译文正确、来源新鲜、分页耗尽或飞书值/链接正确。
