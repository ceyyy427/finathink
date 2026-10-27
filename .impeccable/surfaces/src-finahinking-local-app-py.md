---
version: 1
slug: "src-finahinking-local-app-py"
primary_target: "src/finahinking/local_app.py"
related_targets: ["frontend/src/overview.js"]
---

# 蓝白研究工作台

Scope: local application AppShell and Overview, inherited by existing workspaces. Operate mode for individual researchers. User supplied the executable HTML as a pinned composition and requested implementation without reopening prior autonomous execution decisions.

## Direction contract

THESIS: 从问题进入可验证研究；不以合成收益曲线冒充真实运行。

OWN-WORLD: 用户原型的白色导航、#f5f8fd 底色、#2563eb 主蓝、#edf4ff 问题区、13px 白色面板；系统中文字体与等宽数值。低对比辅助文字加深。

STORY: 看见真实研究活动，写下问题，进入已有任务表单选择数据和授权；再检查证据、比较和复盘。

FIRST VIEWPORT: 224px 左导航、76px 顶栏；中区标题与浅蓝问题输入面板，四项真实记录指标，活动图表与主题；右侧 292px 研究路径和来源卡。主行动位于问题框右下。移动端上下文下移，导航渐进增强为可关闭抽屉。

FORM: 用户已选定 HTML 默认 white 方向，优先于 seed 935b4891 的未选替代；本轮直接按原型代码迁移，不存储未经用户选择的长期工作流偏好。

Signature interaction: 问题通过有界 GET 跳转预填既有任务表单，不自动提交；导航入口搜索和移动抽屉支持键盘与焦点恢复。轻量状态变化，减少动态效果时关闭过渡。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

No new shipping raster is required; reference HTML is supplied by the user, not AI-generated demonstration data.
