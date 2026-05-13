-- Daniel/Emerson 700XA — register blocks for InduVista
-- Run AFTER confirming the GC_SIM_001 device exists.
-- All blocks created with enabled=FALSE; enable each one only after the
-- corresponding tags are inserted and reviewed.

DO $$
DECLARE
  dev_id INTEGER;
BEGIN
  SELECT id INTO dev_id FROM devices WHERE name = 'GC_SIM_001';
  IF dev_id IS NULL THEN
    RAISE EXCEPTION 'Device % not found.', 'GC_SIM_001';
  END IF;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_1001_10', 3, 1001, 10, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_3001_32', 3, 3001, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_3033_32', 3, 3033, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_3065_32', 3, 3065, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_3097_32', 3, 3097, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_3129_32', 3, 3129, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_3161_22', 3, 3161, 22, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_5001_2', 3, 5001, 2, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7017_32', 3, 7017, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7049_32', 3, 7049, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7081_32', 3, 7081, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7113_32', 3, 7113, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7145_32', 3, 7145, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7177_32', 3, 7177, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7209_32', 3, 7209, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7241_32', 3, 7241, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7273_32', 3, 7273, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7305_32', 3, 7305, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7337_32', 3, 7337, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7369_32', 3, 7369, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7401_32', 3, 7401, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7433_32', 3, 7433, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7465_32', 3, 7465, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7497_32', 3, 7497, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7529_32', 3, 7529, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7561_32', 3, 7561, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7593_32', 3, 7593, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7625_32', 3, 7625, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7657_32', 3, 7657, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7689_32', 3, 7689, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7721_32', 3, 7721, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7753_32', 3, 7753, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7785_32', 3, 7785, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7817_32', 3, 7817, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7849_32', 3, 7849, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7881_32', 3, 7881, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7913_32', 3, 7913, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7945_32', 3, 7945, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_7977_32', 3, 7977, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_8009_32', 3, 8009, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_8041_32', 3, 8041, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_8073_32', 3, 8073, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_8105_32', 3, 8105, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_8137_32', 3, 8137, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_8169_32', 3, 8169, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_8201_32', 3, 8201, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_8233_32', 3, 8233, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_8265_29', 3, 8265, 29, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_8963_2', 3, 8963, 2, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_9006_9', 3, 9006, 9, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_9022_32', 3, 9022, 32, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

  INSERT INTO register_blocks
    (device_id, name, function_code, start_address, count, addressing_mode,
     scan_interval_ms, enabled)
  VALUES
    (dev_id, 'GC700XA_9054_7', 3, 9054, 7, 'ENRON_HOLDING',
     5000, FALSE)
  ON CONFLICT (device_id, name) DO NOTHING;

END $$;
