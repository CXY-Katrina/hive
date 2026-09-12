# Hive 前端

React + TypeScript + Vite，运行时依赖仅 `react` / `react-dom`。没有独立前端数据库、UI 框架、路由或图表服务。

```bash
npm ci
npm run dev
npm run build
```

开发服务将 `/api` 代理到 `127.0.0.1:8000`；开发时后端 `HIVE_ORIGIN` 须与浏览器地址一致。生产由中心 API 提供 `dist/` 静态产物，同源 Cookie 会话。请先在项目根目录按主 README 配置 MySQL 与 API。

当前展示页面：`/login`、`/clusters`、`/nodes/`（及 `/nodes/:id`）、`/requests`。任务中心暂时隐藏，`/tasks` 及子路径重定向到 `/nodes/`；TasksPage 源码保留但不在当前页面入口导入。页面直接读取 API，无演示节点或模拟运行数据。

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
