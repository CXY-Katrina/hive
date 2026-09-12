CREATE TABLE IF NOT EXISTS workflow_presets (
 id CHAR(36) PRIMARY KEY, item_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,
 name VARCHAR(128) NOT NULL, tags JSON NOT NULL, workflow JSON NOT NULL,
 source JSON NOT NULL, source_head CHAR(40) NOT NULL, catalog_path VARCHAR(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,
 sha256 CHAR(64) NOT NULL, enabled BOOLEAN NOT NULL DEFAULT FALSE,
 imported_by VARCHAR(64) NOT NULL, imported_at DATETIME(6) NOT NULL,
 approved_by VARCHAR(64), approved_at DATETIME(6),
 UNIQUE(source_head,catalog_path,item_id), INDEX(enabled)
) ENGINE=InnoDB;
