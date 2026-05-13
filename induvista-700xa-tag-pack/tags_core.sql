-- Bulk-insert 108 fiscal-essential 700XA tags.
-- register_count derived from data_type (DB column is NOT NULL; the API
-- auto-derives it but direct SQL has to be explicit).
-- Idempotent: ON CONFLICT (device_id, name) DO NOTHING.

DO $$
DECLARE
  dev_id INTEGER;
BEGIN
  SELECT id INTO dev_id FROM devices WHERE name = 'GC_SIM_001';
  IF dev_id IS NULL THEN
    RAISE EXCEPTION 'Device GC_SIM_001 not found';
  END IF;

  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_1001_10'),
     'DOUT_1', 'Discrete Output 1-5', 'bool', 'ABCD',
     3, 1001, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_1001_10'),
     'DOUT_2', 'Discrete Output 1-5', 'bool', 'ABCD',
     3, 1002, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_1001_10'),
     'DOUT_3', 'Discrete Output 1-5', 'bool', 'ABCD',
     3, 1003, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_1001_10'),
     'DOUT_4', 'Discrete Output 1-5', 'bool', 'ABCD',
     3, 1004, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_1001_10'),
     'DOUT_5', 'Discrete Output 1-5', 'bool', 'ABCD',
     3, 1005, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_1001_10'),
     'DIN_1', 'Discrete Input 1-5', 'bool', 'ABCD',
     3, 1006, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_1001_10'),
     'DIN_2', 'Discrete Input 1-5', 'bool', 'ABCD',
     3, 1007, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_1001_10'),
     'DIN_3', 'Discrete Input 1-5', 'bool', 'ABCD',
     3, 1008, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_1001_10'),
     'DIN_4', 'Discrete Input 1-5', 'bool', 'ABCD',
     3, 1009, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_1001_10'),
     'DIN_5', 'Discrete Input 1-5', 'bool', 'ABCD',
     3, 1010, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'RUN_T', 'Run Time(1/30th Sec)', 'uint16', 'ABCD',
     3, 3033, 1, 's', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'STREAM_NO', 'Last Analy_Stream Number', 'uint16', 'ABCD',
     3, 3034, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'CDT_STREAM_MASK', 'Last Analy_CDT Stream Mask', 'uint16', 'ABCD',
     3, 3035, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'CUR_MONTH', 'Current Month', 'uint16', 'ABCD',
     3, 3036, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'CUR_DAY', 'Current Day', 'uint16', 'ABCD',
     3, 3037, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'CUR_YEAR_YY', 'Current Year', 'uint16', 'ABCD',
     3, 3038, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'CUR_HOUR', 'Current Hour', 'uint16', 'ABCD',
     3, 3039, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'CUR_MINUTE', 'Current Minute', 'uint16', 'ABCD',
     3, 3040, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'START_TIME_MM', 'Last Analy_Start Time', 'uint16', 'ABCD',
     3, 3041, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'START_TIME_DD', 'Last Analy_Start Time', 'uint16', 'ABCD',
     3, 3042, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'START_TIME_YY', 'Last Analy_Start Time', 'uint16', 'ABCD',
     3, 3043, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'START_TIME_hh', 'Last Analy_Start Time', 'uint16', 'ABCD',
     3, 3044, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'START_TIME_mm', 'Last Analy_Start Time', 'uint16', 'ABCD',
     3, 3045, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'SYS_ALARM_BITMAP_3046', '0:Unused, 1:Unused, 2:System Alarm_Alarm On - Last Analysis_Analog Input 1 Low Signal, 3:System Alarm_Alarm On - Last Analysis_Analog Input 1 High Signal, 4:System Alarm_Alarm On - Last Analysis_Analo', 'uint16', 'ABCD',
     3, 3046, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'SYS_ALARM_BITMAP_3047', '0:System Alarm_Alarm On - Current Analysis_Power Failure, 1:Calibration Failed, 2:Preamp Failure, 3:Unused, 4:Unused, 5:Unused, 6:Unused, 7:Unused, 8:Unused, 9:Unused, 10:Unused, 11:Unused, 12:Unused,', 'uint16', 'ABCD',
     3, 3047, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'NDF', 'New Data Flag', 'uint16', 'ABCD',
     3, 3058, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'ANAL_CAL_FLAG', 'Analy/Calib Flag', 'uint16', 'ABCD',
     3, 3059, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'DAILY_AVG_UPDATED', 'Daily Avg Updated', 'uint16', 'ABCD',
     3, 3060, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'LAST_STREAM', 'Last Stream', 'uint16', 'ABCD',
     3, 3061, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'STREAM_NEW_DATA', '2 - Stream 2_New Data Available', 'uint16', 'ABCD',
     3, 3062, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'STREAM_NEW_DATA_2', '3 - Stream 3_New Data Available', 'uint16', 'ABCD',
     3, 3063, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3033_32'),
     'STREAM_NEW_DATA_3', '4 - Stream 4_New Data Available', 'uint16', 'ABCD',
     3, 3064, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3065_32'),
     'STREAM_NEW_DATA_4', '5 - Stream 5_New Data Available', 'uint16', 'ABCD',
     3, 3065, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3097_32'),
     'CALCULATIONS_CONFIGURATION_PRIMARY_CV_UNITS', 'Calculations Configuration_Primary CV Units', 'uint16', 'ABCD',
     3, 3098, 1, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3097_32'),
     'LAST_RUN_VALID_1', 'Last Run Data Valid 1', 'uint16', 'ABCD',
     3, 3099, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3097_32'),
     'LAST_RUN_VALID_2', 'Last Run Data Valid 2', 'uint16', 'ABCD',
     3, 3100, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3097_32'),
     'LAST_RUN_VALID_3', 'Last Run Data Valid 3', 'uint16', 'ABCD',
     3, 3101, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_3097_32'),
     'LAST_RUN_VALID_4', 'Last Run Data Valid 4', 'uint16', 'ABCD',
     3, 3102, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_5001_2'),
     'CYCLE_T', 'Last Analy_Cycle Time (1/30th sec)', 'int32', 'ABCD',
     3, 5001, 2, 's', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_5001_2'),
     'CYCLE_T_2', 'Last Analy_Cycle Time (1/30th sec)', 'int32', 'ABCD',
     3, 5002, 2, 's', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_1', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7017, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_2', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7018, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_3', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7019, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_4', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7020, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_5', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7021, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_6', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7022, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_7', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7023, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_8', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7024, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_9', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7025, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_10', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7026, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_11', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7027, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_12', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7028, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_13', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7029, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_14', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7030, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_15', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7031, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'WEIGHT_16', 'Last Analy_Weight %', 'float32', 'ABCD',
     3, 7032, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'ISO_CV_SUP_DRY_P', 'Last Analy_ISO CV Sup Dry - Pri', 'float32', 'ABCD',
     3, 7033, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'ISO_CV_SUP_SAT_P', 'Last Analy_ISO CV Sup Sat - Pri', 'float32', 'ABCD',
     3, 7034, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'ISO_RHO_REL_P', 'Last Analy_ISO Real Rel Den Gas - Pri', 'float32', 'ABCD',
     3, 7035, 2, 'kg/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'ISO_Z_P', 'Last Analy_ISO Z Factor - Pri', 'float32', 'ABCD',
     3, 7036, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'ISO_WI_SUP_P', 'Last Analy_ISO Wobbe Index Sup - Pri', 'float32', 'ABCD',
     3, 7037, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'TOTAL_UNN_CONC', 'Last Analy_Total Unnormalized Conc', 'float32', 'ABCD',
     3, 7038, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'STREAM_AVG_MW', '1 - Stream 1_Avg Molecular Weight', 'float32', 'ABCD',
     3, 7039, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'CALC_1_TOTAL_SULPHUR', 'Calc Result[1 - TOTAL SULPHUR]', 'float32', 'ABCD',
     3, 7040, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'CALC_2_USER_CAL_2', 'Calc Result[2 - User Cal 2]', 'float32', 'ABCD',
     3, 7041, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'CALC_3_USER_CAL_3', 'Calc Result[3 - User Cal 3]', 'float32', 'ABCD',
     3, 7042, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'CALC_4_USER_CAL_4', 'Calc Result[4 - User Cal 4]', 'float32', 'ABCD',
     3, 7043, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'CALC_5_USER_CAL_5', 'Calc Result[5 - User Cal 5]', 'float32', 'ABCD',
     3, 7044, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'ISO_CV_SUP_DRY_S', 'Last Analy_ISO CV Sup Dry - Sec', 'float32', 'ABCD',
     3, 7046, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'ISO_CV_SUP_SAT_S', 'Last Analy_ISO CV Sup Sat - Sec', 'float32', 'ABCD',
     3, 7047, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7017_32'),
     'ISO_CV_INF_DRY_S', 'Last Analy_ISO CV Inf Dry - Sec', 'float32', 'ABCD',
     3, 7048, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7049_32'),
     'ISO_CV_INF_SAT_S', 'Last Analy_ISO CV Inf Sat - Sec', 'float32', 'ABCD',
     3, 7049, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7049_32'),
     'ISO_Z_S', 'Last Analy_ISO Z Factor - Sec', 'float32', 'ABCD',
     3, 7050, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7049_32'),
     'ISO_RHO_REL_S', 'Last Analy_ISO Real Rel Den Gas - Sec', 'float32', 'ABCD',
     3, 7051, 2, 'kg/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7049_32'),
     'ISO_RHO_KG_M3_S', 'Last Analy_ISO Gas Den kg/m3 - Sec', 'float32', 'ABCD',
     3, 7052, 2, 'kg/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7049_32'),
     'ISO_WI_SUP_S', 'Last Analy_ISO Wobbe Index Sup - Sec', 'float32', 'ABCD',
     3, 7053, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7049_32'),
     'ISO_WI_INF_S', 'Last Analy_ISO Wobbe Index Inf - Sec', 'float32', 'ABCD',
     3, 7054, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7081_32'),
     'AI_1_AI_1', 'Current Value[1 - Analog Input 1]', 'float32', 'ABCD',
     3, 7085, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7081_32'),
     'AI_2_AI_2', 'Current Value[2 - Analog Input 2]', 'float32', 'ABCD',
     3, 7086, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7081_32'),
     'ISO_CV_INF_DRY_P', 'Last Analy_ISO CV Inf Dry - Pri', 'float32', 'ABCD',
     3, 7087, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7081_32'),
     'ISO_CV_INF_SAT_P', 'Last Analy_ISO CV Inf Sat - Pri', 'float32', 'ABCD',
     3, 7088, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7081_32'),
     'ISO_WI_INF_P', 'Last Analy_ISO Wobbe Index Inf - Pri', 'float32', 'ABCD',
     3, 7089, 2, 'MJ/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7081_32'),
     'ISO_RHO_KG_M3_P', 'Last Analy_ISO Gas Den kg/m3 - Pri', 'float32', 'ABCD',
     3, 7090, 2, 'kg/m3', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7081_32'),
     'FCAL_TOTAL_CALIB_RUNS', 'Last FCalib_Total Calibration Runs', 'float32', 'ABCD',
     3, 7091, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7081_32'),
     'FCAL_TOTAL_AVG_RUNS', 'Last FCalib_Total Average Runs', 'float32', 'ABCD',
     3, 7092, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7081_32'),
     'AUTO_CALIB_START_TIME_hhmm', 'Auto Calibration Start Time', 'float32', 'ABCD',
     3, 7093, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7113_32'),
     'CAL_STREAM_NUMBER', 'Last Calib_Stream Number', 'float32', 'ABCD',
     3, 7122, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7113_32'),
     'GSMR_INCOMP_COMBUSTION_FACTOR', 'Last Analy_GS(M)R Incomp Combustion Factor', 'float32', 'ABCD',
     3, 7123, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7113_32'),
     'GSMR_SOOT_INDEX', 'Last Analy_GS(M)R Soot Index', 'float32', 'ABCD',
     3, 7124, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_7113_32'),
     'RATIO_OF_LATENT_HEAT_CAP', 'Last Analy_Ratio of Latent Heat Cap', 'float32', 'ABCD',
     3, 7125, 2, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_8963_2'),
     'CLEAR_ALL_ALARMS', 'Clear All Alarms', 'float32', 'ABCD',
     3, 8963, 2, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_8963_2'),
     'ACK_ALL_ALARMS', 'Acknowledge All Alarms', 'float32', 'ABCD',
     3, 8964, 2, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9006_9'),
     'CUR_MONTH_2', 'Current Month', 'uint16', 'ABCD',
     3, 9006, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9006_9'),
     'CUR_DAY_2', 'Current Day', 'uint16', 'ABCD',
     3, 9007, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9006_9'),
     'CUR_YEAR_YYYY', 'Current Year', 'uint16', 'ABCD',
     3, 9008, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9006_9'),
     'CUR_HOUR_2', 'Current Hour', 'uint16', 'ABCD',
     3, 9009, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9006_9'),
     'CUR_MINUTE_2', 'Current Minute', 'uint16', 'ABCD',
     3, 9010, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9006_9'),
     'CUR_SECOND', 'Current Second', 'uint16', 'ABCD',
     3, 9011, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9006_9'),
     'MODBUS_ID_1_PORT_0', 'Modbus Id[1 - Port 0]', 'uint16', 'ABCD',
     3, 9013, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9006_9'),
     'SITE_ID', 'Site Id', 'uint16', 'ABCD',
     3, 9014, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9022_32'),
     'ANALYSIS_T', 'Analysis Time', 'uint16', 'ABCD',
     3, 9022, 1, 's', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9022_32'),
     'CYCLE_T_3', 'Cycle Time', 'uint16', 'ABCD',
     3, 9024, 1, 's', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9022_32'),
     'RUN_T_2', 'Run Time', 'uint16', 'ABCD',
     3, 9026, 1, 's', 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9022_32'),
     'CUR_STREAM', 'Current Stream', 'uint16', 'ABCD',
     3, 9028, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9022_32'),
     'GC_CONTROL_ANALYSER_CONTROL', 'GC Control_Analyser Control (Write Reg 9030)', 'uint16', 'ABCD',
     3, 9030, 1, NULL, 1, 0,
     TRUE, TRUE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9022_32'),
     'GC_CALIBRATING', 'GC Calibrating', 'uint16', 'ABCD',
     3, 9032, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9022_32'),
     'ACTIVE_ALARM_FLAG', 'Active Alarm Flag', 'uint16', 'ABCD',
     3, 9034, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;
  INSERT INTO tags
    (device_id, register_block_id, name, description, data_type, byte_order,
     function_code, address, register_count, engineering_unit, scale, "offset",
     enabled, writable)
  VALUES
    (dev_id,
     (SELECT id FROM register_blocks WHERE device_id = dev_id AND name = 'GC700XA_9022_32'),
     'UNACK_ALARM_FLAG', 'UnAck Alarm Flag', 'uint16', 'ABCD',
     3, 9035, 1, NULL, 1, 0,
     TRUE, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

END $$;

-- Verify
SELECT b.name AS block_name, COUNT(t.id) AS tag_count
FROM register_blocks b
LEFT JOIN tags t ON t.register_block_id = b.id
WHERE b.name LIKE 'GC700XA_%' AND b.enabled = TRUE
GROUP BY b.name
ORDER BY b.name;
