ALTER TABLE nodes
 ADD COLUMN deleted_at DATETIME(6) NULL,
 ADD COLUMN active_host VARCHAR(255) GENERATED ALWAYS AS (IF(deleted_at IS NULL,host,NULL)) STORED,
 DROP INDEX host,
 ADD UNIQUE KEY active_endpoint(active_host,port);

UPDATE nodes
 SET model=JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.hardware_profile.system_product'))
 WHERE JSON_TYPE(JSON_EXTRACT(metadata,'$.hardware_profile.system_product'))='STRING'
 AND CHAR_LENGTH(JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.hardware_profile.system_product'))) BETWEEN 1 AND 128;
