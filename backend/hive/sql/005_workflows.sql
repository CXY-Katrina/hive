CREATE TABLE IF NOT EXISTS workflow_spaces (
 id CHAR(36) PRIMARY KEY, request_id CHAR(36) NOT NULL UNIQUE,
 owner_user_id CHAR(36) NOT NULL, owner_name VARCHAR(64) NOT NULL,
 status VARCHAR(32) NOT NULL, spec JSON NOT NULL, runtime JSON NOT NULL,
 retain_until DATETIME(6), reason TEXT, created_at DATETIME(6) NOT NULL,
 INDEX(status,created_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS workflow_environments (
 space_id CHAR(36) NOT NULL, alias VARCHAR(32) NOT NULL,
 status VARCHAR(32) NOT NULL, spec JSON NOT NULL, runtime JSON NOT NULL,
 reason TEXT, PRIMARY KEY(space_id,alias),
 FOREIGN KEY(space_id) REFERENCES workflow_spaces(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS workflows (
 id CHAR(36) PRIMARY KEY, space_id CHAR(36) NOT NULL,
 owner_user_id CHAR(36) NOT NULL, owner_name VARCHAR(64) NOT NULL,
 idempotency_key VARCHAR(128) NOT NULL, body_hash CHAR(64) NOT NULL,
 name VARCHAR(128) NOT NULL, spec JSON NOT NULL, status VARCHAR(32) NOT NULL,
 cancel_requested BOOLEAN NOT NULL DEFAULT FALSE, reason TEXT,
 created_at DATETIME(6) NOT NULL, started_at DATETIME(6), ended_at DATETIME(6),
 UNIQUE(owner_user_id,idempotency_key), INDEX(status,created_at),
 FOREIGN KEY(space_id) REFERENCES workflow_spaces(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS workflow_jobs (
 workflow_id CHAR(36) NOT NULL, id VARCHAR(48) NOT NULL,
 status VARCHAR(32) NOT NULL, phase VARCHAR(32) NOT NULL,
 spec JSON NOT NULL, runtime JSON NOT NULL, reason TEXT,
 started_at DATETIME(6), ended_at DATETIME(6),
 PRIMARY KEY(workflow_id,id), FOREIGN KEY(workflow_id) REFERENCES workflows(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS workflow_claims (
 node_id CHAR(36) NOT NULL, kind VARCHAR(8) NOT NULL, resource_key VARCHAR(64) NOT NULL,
 space_id CHAR(36) NOT NULL, workflow_id CHAR(36) NOT NULL, job_id VARCHAR(48) NOT NULL,
 PRIMARY KEY(node_id,kind,resource_key), INDEX(workflow_id,job_id)
) ENGINE=InnoDB;
