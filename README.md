# Eagle 每日精选素材

每天从 Pinterest 和小红书择优收藏3–5组图文，约70%已有偏好、30%探索。作品按实际用途直接复用，只有明确增加价值的补充需求才进入加工，最多1组；没有分析图或Prompt不会生成欠账。

## 当前结果 · 2026-11-08

- 两个平台的登录后图文已实际读取。小红书由用户完成重新登录；没有代填认证信息。
- 3组、8张原图已入库：米白蕾丝礼服现成参考1张、暖调居家针织写真6张、暗色新娘插画1张。原图字节、来源及所选标签/目录已通过API和磁盘回读。
- 第一次导入遇到Eagle列表延迟，保留失败回执；只读核对找到已存条目，没有重复导入。修正后连续三轮小批保存通过，两个平台均有实际入库。
- 1797条旧素材的名称、主来源、注释、标签、目录和星级前后无变化。旧索引、ledger、网页导入记录原文件SHA不变，已保留项目内原字节快照。
- 74项测试通过，两个技能入口校验通过。旧任务已不存在，通过App工具创建唯一替代任务 `eagle-2`，每天北京时间20:00，状态ACTIVE，沿用原模型及high推理。自然调度尚待首次触发验证；新工具未提供自动归档设置字段，未声称已恢复该旧选项。

[第一组预览](state/runs/20261108-trial-1-reconciled/preview.md) · [第二组预览](state/runs/20261108-trial-2/preview.md) · [第三组预览](state/runs/20261108-trial-3/preview.md)

[实测与完整性回执](verification.json) · [调度回读](automation-receipt.json) · [任务提示词](automation-prompt.md)

## 入口与数据

执行规则在项目 `.agents/skills/eagle-daily-workflow/SKILL.md`，详细字段在其 `references/collection.md`。调度器启动具有浏览器和视觉判断能力的Agent，由Agent发现并实际查看素材，再调用脚本。脚本本身不会从网页标题推断画面内容。

解释器：`E:/Python/python.exe`。在项目目录运行：

```powershell
E:/Python/python.exe eagle-workflow/daily.py init
E:/Python/python.exe eagle-workflow/daily.py seeds --out eagle-workflow/work/seed-candidates.json
E:/Python/python.exe eagle-workflow/daily.py plan --manifest 清单.json --out 计划.json
E:/Python/python.exe eagle-workflow/daily.py import --manifest 清单.json --out 保存回执.json
E:/Python/python.exe eagle-workflow/daily.py import --manifest 清单.json --out 保存回执.json --apply
E:/Python/python.exe eagle-workflow/daily.py finish --manifest 清单.json --receipts 保存回执.json --run-id 唯一运行名
E:/Python/python.exe eagle-workflow/daily.py activation
```

导入默认预览。`--apply --reconcile`只核对本组先前登记过的写入，不添加媒体或重发标签/目录。未尝试页、身份不明或仍未保存的值保持阻塞；恢复写入需要明确处理该缺口。

`settings.json`记录当前选材规模和时段。`state/preferences.json`是实看30份原始收藏后形成的暂定偏好，尚不代表用户逐条认可。明确反馈用 `feedback --item-id ID --signal like|dislike|adopted`；自动生成卡与未认可的新自动收藏不参与偏好学习。

`state`保存作品原文、页序、用途、限制、导入意图、执行回执和反馈；`work`保存本轮中间文件；`backups`保存修改前文件。三者留本机且不提交Git。旧目录保留只读，`state/legacy-archive`是原字节快照，封存不代表历史任务完成。18项历史标签/目录保存问题保留独立来源指针，不进入每日续做。

媒体仍保存在Eagle。现成分析图保留外部发布来源，不加本系统“分析成果”标签；已有注释和星级保留，新增注释仅来源网址，原文在侧车。下载沿用本机已有WinINET代理配置，不修改系统设置；支持小红书不带扩展名的WebP地址。

## 验证与恢复

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:TMPDIR=(Resolve-Path 'eagle-workflow/work').Path
E:/Python/python.exe -m pytest eagle-workflow .agents/skills/eagle-daily-workflow/scripts/test_workflow.py C:/Users/36322/.codex/skills/eagle-story-assets/scripts -q --capture=sys
```

覆盖直接复用、普通原图、混合组图、关键页缺失、用途边界、重复/删除、跨来源不明写入、API与磁盘不一致、延迟列表、严格只读核对、清单与执行绑定、部分/零结果、连续三轮和两平台入库门槛。

项目改动和共享技能改动分别提交各自Git仓库。修改前备份清单在 `backups/20261108`；撤回时先核对后续编辑，再逐文件恢复。已收藏媒体和历史证据应保留，不能用清空台账撤销。

---

> **公开快照说明**：本仓库是公开镜像，只收录源码、文档与脚本。成片、素材、模型权重与工程文件
> 留在本地源目录，因此部分链接指向的文件不在此仓库内。导出来源与剔除明细见
> [PUBLISH-NOTE.md](PUBLISH-NOTE.md)。
