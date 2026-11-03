# 平台路线：已验证与待验证

以下是可选的平台路线，Actor套餐、字段和浏览器接口可能变更。首次使用核对 schema、收费/配额及回复能力，不把 SUCCEEDED 当全量证明。

| 平台 | 指标路径 | 评论路径 | 边界 |
|---|---|---|---|
| TikTok | `clockworks/tiktok-scraper` 按视频 URL 批量 | `datadoping/tiktok-comment-reply-scraper`；缺口才用 `clockworks/tiktok-comments-scraper` | 主 Actor 当时 max_replies 上限1000；修正 schema 才成功。备用有 blocked/malformed 响应，仍可能有缺口 |
| Instagram | `apify/instagram-scraper` 按帖子 URL | 先查 Actor 实际配额；历史执行中用已登录浏览器的站点主评/回复游标请求 | 三个免费方案均截断，不宜无依据继续换 Actor。浏览器站点接口不是永久 API |
| YouTube | `streamers/youtube-scraper`，核对 Shorts/watch 视频 ID | `streamers/youtube-comments-scraper`，先 NEWEST_FIRST；缺口才 TOP_COMMENTS | 保存 cid、replyToCid；两种排序仍可能缺隐藏回复 |
| Facebook | `apify/facebook-reels-scraper` 确认准确 Reel；公开 DOM 补计数 | `apify/facebook-comments-scraper`，历史检查中 RANKED_UNFILTERED + includeNestedComments | Actor确认内容但未返回计数；附件/贴纸保留源元数据 |
| Snapchat Spotlight | 对准原视频 ID 解析公开页面 `__NEXT_DATA__` | 历史检查中从 `encodedComments` 读取 | 只验证初始 JSON 的小量评论；未验证大量评论分页能力 |
| Snapchat Story | 原 URL核对；非公开 Insights需创作者资料 | 原Story回复通常不公开 | 不能用主页现有Story/Spotlight替代 |
| X | `xquik/x-tweet-scraper` 精确 status ID | 历史检查中新鲜 replyCount=0，未另启评论 Actor | 非零回复的全量路线尚未由此案例验证 |

## 字段映射

- TikTok：Views=playCount；Likes=diggCount；Saves=collectCount；Comments=commentCount；Shares=shareCount。repostCount不混入Shares。
- Instagram：历史检查中Views使用videoPlayCount；Likes=likesCount；Comments=commentsCount。Saves和总Shares未公开；不任意替换播放/观看字段。
- YouTube：公开播放、点赞、评论；Saves/总Shares不可由公开数据推出。
- X：Saves=bookmarks；Shares=reposts；不与其他平台分享定义直接等同。
- Snapchat：boostCount经历史检查中Like按钮核对为点赞；recommendCount不映射成点赞/转发；viewCount=-1是未公开哨兵。
- Facebook：历史检查中公开DOM给Likes/Comments/Shares，Views/Saves未公开。跨发不可可靠拆分的数值保留未知。

## Instagram 备用细节

历史检查中 `apidojo/instagram-comments-scraper-api` 总计10条；`scrapesmith/instagram-comments-scraper` 免费上限0条；`louisdeconinck/instagram-comments-scraper` 每帖15条主评。这是历史账号套餐表现，不是这些Actor的通用能力上限。

有效站点路径：`/api/v1/media/{media_id}/comments/`、`/api/v1/media/{media_id}/comments/{parent_id}/child_comments/?count=20`。主评游标next_min_id/next_max_id；回复next_min_child_cursor/next_max_child_cursor；合并预览子回复，以pk/id去重。

用当前支持的登录浏览器工具；保存每页状态/游标/时间。遇到权限错误、重复游标、无下一页或页数上限时记录停止原因。不要导出/粘贴cookie或令牌，也不把旧浏览器target直接跑到新任务。

KOX 历史检查中本地 `0.2.0.dev0` 接口是创作者资料/hot_posts，未提供准确视频URL强制刷新；X另需授权，Snapchat不支持。不能据组织列表为空推断公开查询失败，也不能把未做全量查询说成失败。
