CREATE TABLE IF NOT EXISTS users (
 id CHAR(36) PRIMARY KEY, username VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL UNIQUE,
 created_at DATETIME(6) NOT NULL, last_login_at DATETIME(6) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS sessions (
 token_hash CHAR(64) PRIMARY KEY, user_id CHAR(36) NOT NULL, created_at DATETIME(6) NOT NULL,
 expires_at DATETIME(6) NOT NULL, INDEX (expires_at), FOREIGN KEY(user_id) REFERENCES users(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS nodes (
 id CHAR(36) PRIMARY KEY, name VARCHAR(128) NOT NULL, cluster_name VARCHAR(128) NOT NULL DEFAULT 'default',
 host VARCHAR(255) NOT NULL, port INT NOT NULL DEFAULT 22, ssh_user VARCHAR(64) NOT NULL DEFAULT 'root',
 password_cipher TEXT NOT NULL, generation VARCHAR(16) NOT NULL, model VARCHAR(128) NOT NULL,
 adapter VARCHAR(64) NOT NULL DEFAULT 'ascend', vendor VARCHAR(64) NOT NULL DEFAULT 'ascend',
 device_kind VARCHAR(32) NOT NULL DEFAULT 'npu', maintenance BOOLEAN NOT NULL DEFAULT FALSE,
 status VARCHAR(32) NOT NULL DEFAULT 'unknown', reason TEXT, boot_id VARCHAR(64),
 sampled_at DATETIME(6), metadata JSON, mounts JSON, probe_requested BOOLEAN NOT NULL DEFAULT TRUE,
 created_at DATETIME(6) NOT NULL, UNIQUE(host,port)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS devices (
 id CHAR(36) PRIMARY KEY, node_id CHAR(36) NOT NULL, slot VARCHAR(64) NOT NULL,
 command_id VARCHAR(32) NOT NULL, chip_id VARCHAR(32) NOT NULL, logical_id VARCHAR(32) NOT NULL,
 memory_total BIGINT NOT NULL, memory_used BIGINT, ai_core DOUBLE,
 health VARCHAR(32) NOT NULL DEFAULT 'unknown', quality VARCHAR(32) NOT NULL DEFAULT 'unknown',
 reason TEXT, process_complete BOOLEAN NOT NULL DEFAULT FALSE, processes JSON, extensions JSON,
 sampled_at DATETIME(6), boot_id VARCHAR(64), baseline_bytes BIGINT NOT NULL DEFAULT 0,
 baseline_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
 UNIQUE(node_id,slot), FOREIGN KEY(node_id) REFERENCES nodes(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS resource_requests (
 id CHAR(36) PRIMARY KEY, owner_user_id CHAR(36) NOT NULL, owner_name VARCHAR(64) NOT NULL,
 idempotency_key VARCHAR(128) NOT NULL, body_hash CHAR(64) NOT NULL, spec JSON NOT NULL,
 purpose VARCHAR(16) NOT NULL, status VARCHAR(32) NOT NULL, reason TEXT,
 version INT NOT NULL DEFAULT 1, created_at DATETIME(6) NOT NULL, reserved_at DATETIME(6),
 delivered_at DATETIME(6), protected_until DATETIME(6), release_started_at DATETIME(6),
 released_at DATETIME(6), deadline DATETIME(6), release_cause VARCHAR(32), cleanup_started_at DATETIME(6),
 UNIQUE(owner_user_id,idempotency_key), INDEX(status,created_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS device_ownership (
 device_id CHAR(36) PRIMARY KEY, request_id CHAR(36) NOT NULL, epoch INT NOT NULL,
 FOREIGN KEY(device_id) REFERENCES devices(id), FOREIGN KEY(request_id) REFERENCES resource_requests(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS allocation_devices (
 request_id CHAR(36) NOT NULL, device_id CHAR(36) NOT NULL, node_id CHAR(36) NOT NULL,
 epoch INT NOT NULL, locked_at DATETIME(6) NOT NULL, released_at DATETIME(6),
 PRIMARY KEY(request_id,device_id,epoch), INDEX(node_id,locked_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS device_samples (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, device_id CHAR(36) NOT NULL, sampled_at DATETIME(6) NOT NULL,
 boot_id VARCHAR(64), ai_core DOUBLE, memory_used BIGINT, memory_total BIGINT NOT NULL,
 quality VARCHAR(32) NOT NULL, extensions JSON, definition_version INT NOT NULL DEFAULT 1,
 INDEX(device_id,sampled_at), INDEX(sampled_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS connectivity_checks (
 source_device CHAR(36) NOT NULL, target_device CHAR(36) NOT NULL,
 status VARCHAR(32) NOT NULL, detail JSON NOT NULL, checked_at DATETIME(6) NOT NULL,
 PRIMARY KEY(source_device,target_device)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS executions (
 id CHAR(36) PRIMARY KEY, request_id CHAR(36) NOT NULL UNIQUE,
 owner_user_id CHAR(36) NOT NULL, owner_name VARCHAR(64) NOT NULL,
 name VARCHAR(128) NOT NULL, spec JSON NOT NULL, status VARCHAR(32) NOT NULL,
 cancel_requested BOOLEAN NOT NULL DEFAULT FALSE, created_at DATETIME(6) NOT NULL,
 started_at DATETIME(6), ended_at DATETIME(6), reason TEXT,
 FOREIGN KEY(request_id) REFERENCES resource_requests(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS execution_nodes (
 execution_id CHAR(36) NOT NULL, node_id CHAR(36) NOT NULL, attempt INT NOT NULL DEFAULT 1,
 status VARCHAR(32) NOT NULL, remote_path VARCHAR(512) NOT NULL,
 result JSON, log_offset BIGINT NOT NULL DEFAULT 0,
 PRIMARY KEY(execution_id,node_id), FOREIGN KEY(execution_id) REFERENCES executions(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS lease_decisions (
 request_id CHAR(36) PRIMARY KEY, decision VARCHAR(32) NOT NULL, reason TEXT NOT NULL,
 evidence JSON NOT NULL, checked_at DATETIME(6) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS audit_events (
 id CHAR(36) PRIMARY KEY, actor_id VARCHAR(64) NOT NULL, username VARCHAR(64) NOT NULL,
 action VARCHAR(64) NOT NULL, object_id VARCHAR(128) NOT NULL,
 detail JSON NOT NULL, created_at DATETIME(6) NOT NULL, INDEX(created_at), INDEX(object_id,created_at)
) ENGINE=InnoDB;
