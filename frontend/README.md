# Hive 前端

React + TypeScript + Vite，运行时依赖仅 `react` / `react-dom`。没有独立前端数据库、UI 框架、路由或图表服务。

```bash
npm ci
npm run dev
npm run build
```

开发服务将 `/api` 代理到 `127.0.0.1:18000`；开发时后端 `HIVE_ORIGIN` 须与浏览器地址一致。部署页面统一使用中心 API 的 `18000` 端口提供 `dist/` 静态产物，同源 Cookie 会话。请先在项目根目录按主 README 配置 MySQL 与 API。

当前展示页面：`/login`、`/clusters`、`/nodes/`（及 `/nodes/:id`）、`/requests`。任务提交和记录集成在“机器申请”：勾选“新建任务”配置固定 PR、独立 server/client 环境、多个文件入口与作业依赖。独立任务中心菜单仍隐藏，`/tasks` 及子路径重定向到 `/nodes/`；旧 TasksPage 源码保留兼容。页面直接读取 API，无演示节点或模拟运行数据。

视觉恢复原有深绿、草绿与浅色背景，保留等宽标签、卡片层次、按钮微光和登录页浮动卡片等细节。中文为微软雅黑、英文/数字为 Consolas；字体从本机加载，不下载外部字体资源。动画遵守系统的减少动态效果偏好。

- `api.ts`：统一同源请求、错误与会话过期处理。
- `hooks.ts`：可取消轮询、隐藏页面暂停、恢复可见时更新，以及轻量路径导航。
- `types.ts`：前端接口类型。
- `components/ResourceForm.tsx`：申请和任务共用规格表单。
- `components/Assignments.tsx`：申请和任务共用节点/卡分配展示。
- `components/CredentialsModal.tsx`：按操作获取连接凭据，不持久化到浏览器存储。
- `pages/NodesPage.tsx`：从 `/api/metrics` 读取扩展指标，按实际质量展示；历史缺测绘制为曲线断点。
- 各页面仅提交操作并展示服务端状态，不自行执行调度、归还或空闲判断。

`npm run typecheck` 验证接口类型；`npm run build` 包含类型检查并生成可部署静态文件。锁文件固定完整构建依赖。


任务编排组件：

- `WorkflowComposer`：普通申请以外的任务提交、PR 固定、资源/空间复用与保留时间。
- `WorkflowEnvironmentEditor` / `WorkflowFiles`：独立环境输入、Shell/Python/YAML 引用与上传。YAML 必须指定 PR 中的外部执行器；上传执行代码由 API 核对 PR 内容。
- `WorkflowGraph` / `WorkflowJobEditor`：React/SVG 连线、表单依赖、服务 READY 条件、前后检查和零 NPU 客户端。
- `WorkflowPresets`：外部清单标签筛选，只载入已启用条目；管理员从固定 PR 导入 JSON 清单并单项启用后端允许的样例。
- `WorkflowRecords`：任务分阶段日志、取消及运行空间关闭；界面只展示 API 返回状态。

浏览器验收使用开发依赖 Playwright，不增加页面运行时依赖：

```bash
npm ci
npm run test:browser
```

测试先构建静态页面，再在本机随机端口启动独立测试服务器，拦截全部 API，不使用真实数据库、节点或业务 SSH。Windows 优先使用已安装的 Edge；可用 `HIVE_TEST_BROWSER` 指定浏览器可执行文件路径。其他系统可先运行 `npx playwright install chromium` 安装测试浏览器。测试覆盖普通申请兼容、同机双环境、PR 输入竞争、权限、混合文件与 YAML 执行器、依赖连线拒环、服务就绪、零 NPU 客户端、环境复用、日志/取消/关闭、预置筛选、管理员清单导入/单项启用及取消中的状态核验提示。
