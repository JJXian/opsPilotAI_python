# 精确标识符运维检索手册

本手册用于记录错误码、告警编号、服务名、日志路径、JVM 参数和数据库连接地址等精确标识符的处置流程。

## ORA-12514：Oracle 服务名无法识别

**现象**：应用连接 Oracle 时出现 `ORA-12514: TNS:listener does not currently know of service requested in connect descriptor`。

**排查步骤**：

1. 在数据库服务器执行 `lsnrctl status`，确认监听器已启动并检查已注册的 Service Name。
2. 核对应用连接串中的 `SERVICE_NAME` 与数据库实际服务名是否一致。
3. 检查数据库动态注册配置 `LOCAL_LISTENER`，必要时执行 `ALTER SYSTEM REGISTER`。
4. 修改连接配置后重启 `payment-service` 的连接池，再验证连接成功率。

## OOMKilled：Kubernetes 容器内存被终止

**现象**：Pod 状态显示 `OOMKilled`，或 `kubectl describe pod` 中显示容器因内存限制被终止。

**排查步骤**：

1. 使用 `kubectl describe pod <pod-name>` 查看 Last State、内存 limit 和 request。
2. 使用 `kubectl top pod` 检查容器实际内存使用率。
3. 排查 Java 堆、缓存、大对象和批处理任务；不要只通过重启掩盖问题。
4. 完成根因修复后，再合理调整 Deployment 的 `resources.limits.memory`。

## ALERT-OPS-7788：payment-service 下游支付超时

**告警定义**：`ALERT-OPS-7788` 表示 `payment-service` 连续 3 分钟调用支付网关超时比例超过 20%。

**排查步骤**：

1. 在 `payment-service` 日志中按 `trace_id`、`gateway_timeout` 和 `ALERT-OPS-7788` 查询失败请求。
2. 检查支付网关的网络延迟、DNS 解析和 HTTP 连接超时配置。
3. 临时启用支付查询降级和重试限流，避免请求堆积。
4. 超时恢复后检查告警是否自动恢复，并复盘失败订单。

## /var/log/app/error.log：应用错误日志持续增长

**现象**：`/var/log/app/error.log` 文件增长过快，可能导致磁盘空间耗尽。

**处置步骤**：

1. 执行 `du -h /var/log/app/error.log` 确认文件大小，并检查错误日志是否存在重复堆栈。
2. 使用 `logrotate` 配置按大小或按天轮转，压缩历史日志并设置保留周期。
3. 不能直接删除正在写入的文件；紧急情况下可在确认后安全截断并保留文件句柄。
4. 修复持续报错的根因，避免日志再次快速增长。

## -Xmx4096m：JVM 最大堆内存配置

**说明**：`-Xmx4096m` 表示 JVM 最大堆内存为 4096 MB。配置前要为 Metaspace、线程栈、Direct Memory 和操作系统预留内存。

**排查步骤**：

1. 结合容器内存 limit 判断 `-Xmx4096m` 是否过高，避免容器出现 `OOMKilled`。
2. 通过 GC 日志、`jmap -heap <pid>` 和监控数据判断是否真的需要增加堆。
3. 同时设置合理的 `-Xms`，并在压测环境验证 Full GC、暂停时间和峰值内存。

## 10.23.4.15:3306：MySQL 连接被拒绝

**现象**：`payment-service` 连接 `10.23.4.15:3306` 失败，日志可能出现 `Connection refused`、`Communications link failure` 或 `Too many connections`。

**排查步骤**：

1. 从应用节点执行 `nc -vz 10.23.4.15 3306`，确认网络和端口连通。
2. 检查 MySQL 实例状态、监听地址、防火墙和安全组规则。
3. 查看 MySQL `max_connections`、活跃连接数和慢查询，排查连接池泄漏。
4. 恢复后观察 `payment-service` 连接池指标和错误率，确认连接已稳定。
