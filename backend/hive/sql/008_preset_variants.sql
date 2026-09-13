CREATE TABLE IF NOT EXISTS workflow_preset_variants (
 id CHAR(36) PRIMARY KEY,
 parent_id CHAR(36) NOT NULL, root_id CHAR(36) NOT NULL,
 owner_user_id CHAR(36) NOT NULL, owner_name VARCHAR(64) NOT NULL,
 name VARCHAR(128) NOT NULL, tags JSON NOT NULL, workflow JSON NOT NULL,
 source JSON NOT NULL, sha256 CHAR(64) NOT NULL, created_at DATETIME(6) NOT NULL,
 INDEX(owner_user_id), INDEX(root_id),
 CONSTRAINT fk_preset_variant_owner FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
