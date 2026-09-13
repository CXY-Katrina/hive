# Hive 前端

React + TypeScript + Vite，运行时依赖仅 `react` / `react-dom`。没有独立前端数据库、UI 框架、路由或图表服务。

```bash
npm ci
npm run dev
npm run build
```

开发服务将 `/api` 代理到 `127.0.0.1:18000`；开发时后端 `HIVE_ORIGIN` 须与浏览器地址一致。部署页面统一使用中心 API 的 `18000` 端口提供 `dist/` 静态产物，同源 Cookie 会话。请先在项目根目录按主 README 配置 MySQL 与 API。

当前展示页面：`/login`、`/clusters`、`/nodes/`（及 `/nodes/:id`）、`/requests`。任务提交和记录集成在“机器申请”：点击“+ 新建任务”配置固定 PR、独立容器环境、多个文件入口与作业依赖。独立任务中心菜单仍隐藏，`/tasks` 及子路径重定向到 `/nodes/`；旧 TasksPage 源码保留兼容。页面直接读取 API，无演示节点或模拟运行数据。

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
- `WorkflowEnvironmentEditor` / `WorkflowFiles`：节点/容器与镜像配置、Shell/Python/YAML 多文件上传和启动命令。启动命令在所选容器内执行，YAML 通过命令读取；服务器按文件名匹配固定 PR 并核对执行代码，重名时可在文件旁选择重命名，补充 PR 相对路径。
- `WorkflowGraph` / `WorkflowJobEditor`：资源、环境和 Jobs 上下分区，环境和作业分别使用内部 Tabs；React/SVG 连线编辑依赖；服务 READY 条件、前后检查和零 NPU 客户端。作业默认 10 分钟，产物按环境与节点的精确多选组合、名称和路径声明，不要求选择文件类型。
- `WorkflowPresets`：外部清单标签筛选，只载入已启用条目；管理员从固定 PR 导入 JSON 清单并单项启用后端允许的样例。
- `WorkflowRecords`：任务分阶段日志、取消及运行空间关闭；界面只展示 API 返回状态。

浏览器验收使用开发依赖 Playwright，不增加页面运行时依赖：

```bash
npm ci
npm run test:browser
```

测试先构建静态页面，再在本机随机端口启动独立测试服务器，拦截全部 API，不使用真实数据库、节点或业务 SSH。Windows 优先使用已安装的 Edge；可用 `HIVE_TEST_BROWSER` 指定浏览器可执行文件路径。其他系统可先运行 `npx playwright install chromium` 安装测试浏览器。测试覆盖普通申请兼容、同机双环境、PR 输入竞争、权限、混合文件与 YAML 执行器、依赖连线拒环、服务就绪、零 NPU 客户端、环境复用、日志/取消/关闭、预置筛选、管理员清单导入/单项启用及取消中的状态核验提示。

环境申请始终在任务配置之前，点击环境申请标题右侧的“+ 新建任务”后保留已选资源。所有选择使用按钮组。任务命令可以引用 `${node0.ip}`、`${host}`、`${container_name}`；脚本可用 `hive_resource model 逻辑名称` 查询各服务器冻结的模型/数据集/镜像/安装包映射。

草稿自动保存到当前浏览器的 IndexedDB，按用户名隔离，包含资源规格、PR 固定版本、环境/作业参数、上传文件内容及各自选中的 Tab。切换页面或刷新后恢复；容量使用浏览器站点存储配额，配额不足或存储不可用时页面显示错误并保留当前内存输入。清除该站点的浏览器数据会清除草稿。缓存字段仅来自任务配置，不写入平台 SSH 凭据或会话 Cookie。

环境可多选节点；对应作业默认在该环境全部节点上运行。旧预置的单节点字段保持兼容。预置可编辑后“另存为新用例”，保存为本人可见的参数变体并标记尚未验证，不改动原用例、不分配资源；复用空间时另存会保存可重建的资源/环境模板，不保留临时空间 ID。
