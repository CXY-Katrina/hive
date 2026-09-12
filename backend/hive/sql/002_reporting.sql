CREATE TABLE IF NOT EXISTS device_rollups (
 device_id CHAR(36) NOT NULL, resolution INT NOT NULL, bucket_at DATETIME(6) NOT NULL,
 ai_sum DOUBLE NOT NULL DEFAULT 0, ai_valid_seconds DOUBLE NOT NULL DEFAULT 0,
 ai_min DOUBLE, ai_max DOUBLE, active_seconds DOUBLE NOT NULL DEFAULT 0,
 memory_sum DOUBLE NOT NULL DEFAULT 0, memory_valid_seconds DOUBLE NOT NULL DEFAULT 0,
 memory_max BIGINT, expected_seconds DOUBLE NOT NULL, definition_version INT NOT NULL DEFAULT 1,
 PRIMARY KEY(device_id,resolution,bucket_at), INDEX(resolution,bucket_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS worker_state (
 state_key VARCHAR(64) PRIMARY KEY, value_text VARCHAR(255) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS process_events (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, device_id CHAR(36) NOT NULL,
 sampled_at DATETIME(6) NOT NULL, boot_id VARCHAR(64), processes JSON NOT NULL,
 INDEX(device_id,sampled_at), INDEX(sampled_at)
) ENGINE=InnoDB;
