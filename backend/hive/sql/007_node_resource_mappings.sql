CREATE TABLE IF NOT EXISTS node_resource_mappings (
    node_id VARCHAR(36) PRIMARY KEY,
    version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    entries JSON NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    updated_by VARCHAR(128) NOT NULL,
    CONSTRAINT fk_node_resource_mappings_node FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
) ENGINE=InnoDB;
