# 节点资源映射

资源映射属于节点台账，独立于申请、工作流和容器执行。管理员登记逻辑名称与本机已有资源的位置；所有已登录用户可以查询。登记仅保存配置，不连接节点、不下载资源，也不表示文件或镜像已核验存在。

| kind | 逻辑名称 name | 本机位置 target |
|---|---|---|
| `model` | ModelScope `组织/模型名称` | 权重的本机绝对路径 |
| `dataset` | ModelScope `组织/数据集名称` | 数据集的本机绝对路径 |
| `image` | 镜像逻辑名称 | 本机镜像名、标签或完整摘要 |
| `package` | 依赖包逻辑名称 | 安装包的本机绝对路径 |

每个节点最多 128 条、规范化 JSON 的 UTF-8 内容最多 16 KiB。同一类型下名称唯一，区分大小写。路径禁止父目录跳转、控制字符和 Windows 路径；路径中的空格可以保留。这里登记的是宿主机路径，容器能否访问该路径取决于其实际挂载配置。

`GET /api/nodes/{node_id}/mappings` 返回：

```json
{
  "node_id": "节点 UUID",
  "version": 1,
  "entries": [{"kind": "model", "name": "Qwen/Qwen3-8B", "target": "/mnt/weight/Qwen3-8B"}],
  "updated_at": "2026-09-12T00:00:00Z",
  "updated_by": "admin"
}
```

从未登记时返回 `version: 0`、空 `entries`，更新时间和管理员为空。节点已移除或不存在时返回 404；未登录返回 401。

管理员使用 `PUT /api/nodes/{node_id}/mappings` 提交 `{"version": 当前版本, "entries": 完整列表}`。新增、修改和删除通过整表替换完成，空列表清除所有映射。更新在节点行锁内比较版本，旧版本返回 409，不覆盖其他管理员的修改；成功后版本加一，记录 `node.mappings.replace` 审计事件。普通成员写入返回 403。

`POST /api/nodes` 可选携带 `mappings` 列表，省略即不登记。映射与节点在同一个 MySQL 事务内保存。前端纳管表单提供权重、数据集、镜像、依赖包四个添加按钮；节点详情和集群检测详情提供查询及管理员编辑入口。

模块依赖仅为既有 MySQL/PyMySQL、Pydantic/FastAPI、React。数据库迁移为 `007_node_resource_mappings.sql`。Python 服务为 `NodeMappings(db)`，服务容器中的 `node_mappings` 与 `workflows.node_mappings` 指向同一实例；执行侧读取 `get(node_id)` 后自行冻结版本和条目。修改节点台账不会主动修改已保存的工作流快照。

验证命令：

```text
HIVE_TEST_MYSQL_PORT=13316 python -m unittest tests.test_node_mappings
cd frontend
npm run build
node --test tests/node-mappings.browser.mjs
```

API 测试使用随机独立 MySQL 数据库，纳管测试只在 SSH 边界提供响应夹具；浏览器测试使用拦截的 API 响应，不写入纳管节点和生产台账。
