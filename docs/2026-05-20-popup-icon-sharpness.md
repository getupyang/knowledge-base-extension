# 2026-05-20 · Popup icon sharpness fix

- 记录时间：2026-05-20
- 发生时间：2026-05-20 popup 本地验收后
- 关联 commit：`c1fc77d4b486959312784c3483a8d244b88bbf87`
- 分支：`main`
- 涉及文件：`src/popup/index.html`
- 改了什么：popup 顶部品牌图标继续以 `36x36` 显示，但图源从 `icon32.png` 改为 `icon128.png`，避免浏览器把 32px 位图放大后变糊。
- 为什么改：popup 是用户打开插件后第一眼看到的品牌入口，图标清晰度会直接影响产品质感和可信度。
- 用户如何验收：在 `chrome://extensions` 刷新 Margin 插件，重新打开 popup；预期左上角品牌图标边缘和内部白色线条更清楚。
- 已验证：`git diff --check HEAD^..HEAD`；`node --check src/popup/popup.js`；用 Node 检查 popup 图源为 `icon128.png`、天然尺寸 `128x128`、显示尺寸 `36x36`；`scripts/kb-health` 服务项正常，但当时有 1 条既有 `margin_cloud_sync` 24h 警告。
- 适用范围：只影响 Chrome extension popup 顶部图标，不改变 toolbar icon、manifest icon、content script、notebook、后端服务或数据。
- 可能过时的地方：如果后续 popup header 尺寸、品牌素材路径、manifest icon 结构或打包流程改变，这条记录只代表 2026-05-20 的实现状态。
