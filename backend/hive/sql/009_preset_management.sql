CREATE TABLE IF NOT EXISTS workflow_public_presets (
 id CHAR(36) PRIMARY KEY,
 name VARCHAR(128) NOT NULL,
 tags JSON NOT NULL,
 workflow JSON NOT NULL,
 source JSON NOT NULL,
 yaml_path VARCHAR(512) NOT NULL DEFAULT '',
 sha256 CHAR(64) NOT NULL,
 created_by VARCHAR(64) NOT NULL,
 created_at DATETIME(6) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS workflow_preset_remarks (
 preset_id CHAR(36) PRIMARY KEY,
 remarks TEXT NOT NULL,
 updated_by VARCHAR(64) NOT NULL,
 updated_at DATETIME(6) NOT NULL
) ENGINE=InnoDB;
