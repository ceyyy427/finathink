---
name: Finathink
description: 白色导航、浅蓝问题面板与可检查证据组成的蓝白研究工作台
colors:
  background: "#f5f8fd"
  surface: "#fff"
  surface-elevated: "#f8faff"
  surface-raised: "#edf4ff"
  border: "#e6ecf4"
  border-strong: "#b6c7df"
  text-primary: "#172b4d"
  text-secondary: "#62728a"
  evidence: "#2563eb"
  learning: "#1746b8"
  success: "#17806a"
  warning: "#925b10"
  error: "#b43e49"
  control-text: "#38577f"
  success-surface: "#edf7f3"
  warning-surface: "#fff7e8"
  error-surface: "#fff0f1"
typography:
  question-title:
    fontFamily: '"PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "27px"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-.025em"
  headline:
    fontFamily: '"PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "25px"
    fontWeight: 650
    lineHeight: 1.45
    letterSpacing: "-.025em"
  title:
    fontFamily: '"PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "16px"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-.02em"
  panel-title:
    fontFamily: '"PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "14px"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-.02em"
  body:
    fontFamily: '"PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: '"PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.65
  action:
    fontFamily: '"PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.65
  navigation:
    fontFamily: '"PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.65
  status:
    fontFamily: '"PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1.65
    letterSpacing: "0"
  metric:
    fontFamily: '"PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "25px"
    fontWeight: 600
    lineHeight: 1.65
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
rounded:
  sm: "8px"
  md: "13px"
  lg: "13px"
  composer: "9px"
  tag: "5px"
  topbar-action: "7px"
spacing:
  space-1: "4px"
  space-2: "8px"
  space-3: "12px"
  space-4: "16px"
  space-5: "20px"
  space-6: "24px"
  space-8: "32px"
components:
  button-primary:
    backgroundColor: "{colors.evidence}"
    textColor: "{colors.surface}"
    typography: "{typography.action}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
  button-primary-hover:
    backgroundColor: "{colors.learning}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.control-text}"
    typography: "{typography.action}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
  button-secondary-hover:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.learning}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-primary}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "9px 11px"
  nav-link:
    textColor: "{colors.text-secondary}"
    typography: "{typography.navigation}"
    rounded: "{rounded.sm}"
    padding: "10px 12px"
  nav-link-active:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.evidence}"
  suggestion-chip:
    backgroundColor: "#f6f9ff"
    textColor: "#385f97"
    rounded: "{rounded.tag}"
    padding: "5px 9px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "21px 22px"
  question-composer:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.composer}"
    padding: "12px 14px"
  status-evidence:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.evidence}"
    typography: "{typography.status}"
    rounded: "{rounded.tag}"
    padding: "3px 9px"
  status-complete:
    backgroundColor: "{colors.success-surface}"
    textColor: "{colors.success}"
    typography: "{typography.status}"
    rounded: "{rounded.tag}"
    padding: "3px 9px"
  status-offline:
    backgroundColor: "{colors.warning-surface}"
    textColor: "{colors.warning}"
    typography: "{typography.status}"
    rounded: "{rounded.tag}"
    padding: "3px 9px"
  status-limitation:
    backgroundColor: "{colors.error-surface}"
    textColor: "{colors.error}"
    typography: "{typography.status}"
    rounded: "{rounded.tag}"
    padding: "3px 9px"
---

# Design System: Finathink

## Overview

**Creative North Star: "蓝白研究工作台"**

“蓝白研究工作台”以白色导航、冷白蓝底色和浅蓝问题面板建立安静、可检查的研究环境。主要行动、当前入口与数据强调共享研究蓝，墨色标题与加深的辅助蓝灰维持清楚的阅读层次。

界面使用系统中文无衬线字体、细边框和留白组织密集信息；代码使用等宽字体，数值使用等宽数字。常规面板保持平面感，搜索浮层才通过遮罩与投影建立临时层级。视觉材料来自文字、内联线性图标和实际数据图表。

**Key Characteristics:**

- 白色导航与冷白蓝工作台底色。
- 浅蓝问题面板、白色输入区与明确的蓝色行动。
- 细边框、轻圆角与平面的常规内容卡。
- 系统中文字体、等宽代码与对齐的数值。
- 上下文随宽度下移；键盘焦点与减少动态效果偏好贯穿组件。

## Colors

主蓝与浅蓝负责行动和研究强调；冷白、墨色与辅助蓝灰构成阅读环境。规范色值只在前置令牌中定义。

### Primary

- **研究蓝（evidence）**：主要按钮、当前导航、链接、活动柱形与问题标题中的强调。
- **深研究蓝（learning）**：蓝色行动的悬停色，同时服务学习、解释与主题文字。
- **浅蓝问题底（surface-raised）**：问题区、当前导航、证据标签及选中数据行的背景。

### Neutral

- **冷白蓝底（background）**与**白色面板（surface）**：区分工作台画布、导航和内容面板。
- **静态浅层（surface-elevated）**：安静卡片、代码、输入提示与检查区域的底色。
- **细分隔线（border）**与**清晰控件线（border-strong）**：分别组织面板、表格与表单控件。
- **墨色正文（text-primary）**：标题、输入文字与关键数值。
- **辅助蓝灰（text-secondary）**：说明、标签与次要文字；既有 muted 语义也落在这一颜色。
- **控件蓝灰（control-text）**：次要行动和上下文链接的文字。

### Semantic States

- **完成绿（success / success-surface）**：完成、就绪与事实状态。
- **边界棕（warning / warning-surface）**：离线、样例与假设状态。
- **限制红（error / error-surface）**：错误、限制及现有未知状态标签。颜色旁保留明确文字。

**The Blue Action Rule.** 研究蓝同时标记主要行动、当前入口与数据强调；颜色的含义由相邻文字和状态标签一起说明。

## Typography

**Display Font / Body Font:** 系统中文字体栈，首选 PingFang SC、Microsoft YaHei，随后使用平台无衬线字体。

**Label/Mono Font:** 标签继承正文；代码使用 code 令牌。品牌标记中的 Georgia 字母与学习概念中的数学符号是局部材料，不扩展成展示字体。

**Character:** 字体统一而克制，依靠字号、字重、行高和位置区分层次。没有为工作台加载额外的展示字体。

### Hierarchy

- **question-title**：浅蓝问题面板的主句；宽屏与窄屏尺寸随实际断点变化。
- **headline**：页面主标题；移动宽度使用更紧凑的尺寸。
- **title**：一般工作区分区标题。
- **panel-title**：总览内容面板标题；上下文标题使用相同字号、沿用普通标题字重。
- **body**：桌面正文；移动宽度提升正文和表单输入字号。
- **label / action / navigation / status**：分别服务问题标签、小行动、主导航和状态。当前导航增加字重；建议标签保留自己的局部样式。
- **metric**：总览指标。指标与数据表使用等宽数字；并非把所有数值改为等宽字体。
- **code**：代码和需要逐字符检查的内容。

正文与导语的既有阅读宽度由 measure（72ch）限制；表格、卡片和输入区按照各自容器布局。实际层级是按用途选择的字号集合，不假定固定比例。

**The Shared Outline Rule.** 交互元素共享可见的焦点轮廓，焦点不能只靠背景换色表达。

## Layout

常规桌面外壳为左导航（224px）加可伸缩主框架；顶栏高（76px）。工作区在主内容与上下文列（292px）之间留出间隔（23px），最大宽度（1740px），常规内边距（31px 34px 24px）。总览标题横跨主内容与上下文列；两列正文在标题下方对齐。其他工作区沿用同一外壳，保留各自的内容结构。

基础间距沿用前置令牌中的 space 系列。总览内容组以局部间距（20px）相接；面板内边距由各组件令牌定义。表格在自身容器内滚动，长问题、主题及标识允许换行，内容列保留可收缩空间。

### Responsive Behavior

- **宽屏（min-width: 1600px）**：顶栏左右留白（42px）；工作区内边距（35px 42px 24px）；问题面板内边距（29px），问题标题（31px）。
- **紧凑桌面（max-width: 1250px）**：导航（200px）、上下文（255px）、顶栏左右留白（24px）、工作区内边距（25px 24px）与列间距（18px）；隐藏搜索快捷键提示。
- **上下文下移（max-width: 1050px）**：工作区成为单列，上下文接在内容之后；搜索文字和本地状态文字隐藏，搜索按钮保留可访问名称。
- **既有内容折叠（max-width: 880px）**：通用双列内容、知识符号、步骤流等既有工作区转为单列；工作台指标保留两列。外壳移动导航由后续移动断点接管。
- **移动外壳（max-width: 800px）**：正文与输入（16px），主标题（23px），顶栏高（66px），工作区内边距（22px 18px）。启用脚本后导航为可关闭抽屉（224px），关闭时不进入键盘路径；无脚本时保留文档内的双列导航。
- **实验表单（max-width: 760px）**：实验工作区表单变为单列。
- **窄屏（max-width: 560px）**：问题面板内边距（20px 17px），问题标题（25px）且蓝色后半句另起一行；输入容器内边距（11px）；指标排列为两列；常规面板内边距（19px 17px）。

## Elevation & Depth

常规卡片、当前导航和数据面板不使用投影；层次来自白色与浅蓝底色、细边框、内外间距及内容顺序。搜索对话框使用柔和投影，配合深墨色半透明遮罩；移动导航遮罩沿用同一遮罩材料。公共中等投影变量保留为可用词汇，但并不用于抬高常规卡片。

### Shadow Vocabulary

- **Flat surface**：常规卡片与导航的静态深度。
- **Shared medium**：公共变量中的柔和投影；不是默认卡片状态。
- **Search overlay**：搜索对话框的明确浮层投影。
- **Selected data row**：数据行内侧的细蓝线，用于选中状态，不表达悬浮。

精确投影、焦点轮廓、遮罩与动态值记录在侧车文件的 extensions 中。

**The Quiet Surface Rule.** 常规面板用白色、细边框与间距分层；搜索浮层使用遮罩与明显投影。

## Shapes

面板采用 md / lg 圆角，常规控件采用 sm 圆角。问题输入容器使用 composer 圆角；状态与建议标签使用 tag 圆角；顶栏主行动使用 topbar-action 圆角。品牌标记、来源小块和步骤圆点保持各自的局部形状，不构成额外的全局圆角刻度。

内容面板和输入使用细实线边框；空状态通常用虚线边框，加载状态改为实线，错误状态具有局部的淡红底与边线。图标为内联线性 SVG；数学概念可以使用其实际符号。

## Components

### Buttons

主要行动为研究蓝实底、白字和轻圆角。问题框主行动采用 button-primary 令牌，桌面最小高度（38px）；移动外壳下提升到（44px）。其他表单沿用公共按钮的最小高度（44px）和局部内边距（9px 15px）。顶栏主行动另有较紧凑的桌面尺寸。

主要行动悬停转为深研究蓝，不增加位移；禁用按钮降低不透明度（.55）并使用不可用指针。实验工作区的局部禁用规则为（.45）。次要行动为白底、蓝灰字与清晰边框；悬停使用浅蓝底、深蓝字和更明显的边线。焦点沿用共享轮廓。

### Chips and Status Tags

建议标签是带边框的真实链接，使用自身的浅蓝局部配色；悬停时边框转为研究蓝、底色转白。移动宽度下提供更大的触达高度（44px）。

状态标签是文本信息，不添加点击、悬停或选中行为。它们通过完成、证据、离线与限制等已有变体搭配文字和颜色。

### Cards / Containers

白色内容卡以细边框和 md 圆角围合，使用 card 令牌中的常规内边距；窄屏按 Layout 收紧。安静卡片改用静态浅层底色；证据与结果卡有局部的浅蓝边线。卡片本身不提供悬浮或整卡点击状态，内部链接保留正常的悬停与焦点样式。

### Inputs / Fields

输入、选择框与文本区使用白底、清晰边线、sm 圆角及 input 令牌中的内边距；占位文字使用辅助蓝灰，插入光标使用研究蓝。表单标签与帮助文字紧靠对应控件。

问题输入是签名组合：浅蓝面板包住白色输入容器；容器内出现焦点时边线转蓝，文本区保留独立可见焦点与垂直调整大小。按钮旁的文字先解释提交边界。该表单通过 GET 预填既有任务表单，输入本身不自动创建任务。

### Navigation and Search

导航以线性图标、文字和轻圆角条目构成；默认辅助蓝灰，悬停使用冷白蓝底与墨色文字，当前页使用浅蓝底、研究蓝和更高字重。当前页保留 aria-current 语义。

移动抽屉支持关闭按钮、遮罩和 Escape；打开时约束焦点，关闭后恢复到菜单按钮。搜索采用原生对话框，打开后聚焦输入，关闭后恢复之前的有效焦点；快捷键为 Command / Control + K。搜索名称明确为“搜索功能入口”，图标模式也保留该名称。

### Data and Motion

活动柱形使用研究蓝，主题单元以现有浅蓝强度级别呈现；图表旁保留对应的文字记录与数据表。数据点的焦点与悬停可以更新说明区域，不能把研究记录数量表达为投资绩效。

按钮状态过渡使用短时 ease；移动抽屉仅做水平进出。减少动态效果偏好开启时，关闭非必要动画和过渡、使用即时滚动，活动图表同时停用动画。侧车片段只重现已存在的状态，不添加新的交互。

## Do's and Don'ts

### Do:

- **Do** 沿用白色导航、冷白蓝底色、浅蓝问题区与研究蓝主行动的关系。
- **Do** 让正文、标题与操作继承系统中文字体；代码使用等宽字体，数据表与指标保留等宽数字。
- **Do** 用文字配合状态颜色，保留键盘可见焦点、可关闭的移动导航和焦点恢复。
- **Do** 按实际断点下移上下文，并让宽表格在自身容器内横向滚动。

### Don't:

- **Don't** 把普通内容卡变成带悬浮位移、装饰渐变或强投影的视觉主角。
- **Don't** 将品牌旁的微型英文说明字号推广为常规操作、正文或标题样式。
- **Don't** 用字符图形替代现有的内联 SVG 导航图标；数学符号仍用于数学概念。
- **Don't** 给当前工作台引入外部展示字体或无关的栅格插画。

未固化：品牌旁既有的微型英文说明仅属于当前字标，不作为可复用排版层级。本文没有把局部品牌材料或未使用的旧样式推广为新组件规则。
