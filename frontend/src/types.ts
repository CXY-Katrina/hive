export type ID = string | number;
export interface User { id: ID; username: string; admin: boolean }
export interface ProcessInfo { pid: number; name?: string; command?: string; container_name?: string | null; container_id?: string | null; container_status?: string; container_kind?: string; reason?: string }
export interface Device {
  id: ID; slot: string | number; ai_core: number | null; memory_used: number | null; memory_total: number | null;
  health: string; quality: string; sampled_at?: string; reason?: string; processes: ProcessInfo[]; owner_name?: string | null;
  request_id?: ID | null; status: string; protected_until?: string | null; extensions?: Record<string, number | null>;
}
export interface Mount { target?: string; path?: string; source?: string; fstype?: string; status?: string; shared_storage_id?: string; total_bytes?: number; available_bytes?: number; free_bytes?: number; writable?: boolean; readable?: boolean; reason?: string; detail?: string; checked_at?: string }
export interface HardwareProfile {
  system_product: string | null; board_product: string | null; soc_versions: string[];
  devices: { slot: string | number; soc_version: string | null; chip_version: string | null }[];
  quality: 'ok' | 'partial' | 'unknown'; checked_at: string; source: string; reason?: string;
}
export interface ComputeSpec {
  fp16_tflops_per_module: number; basis: 'FP16 dense, dual-die module'; source: string;
  updated_at: string; confirmed_soc_versions: string[];
}
export interface NodeMetadata extends Record<string, unknown> { hardware_profile?: HardwareProfile | null; compute_spec?: ComputeSpec | null }
export interface NodeInfo {
  id: ID; name: string; host: string; port: number; ssh_user: string; cluster_name: string; generation: string; model: string; model_label?: string;
  status: string; reason?: string; maintenance: boolean; sampled_at?: string; metadata: NodeMetadata;
  mounts: Mount[]; devices: Device[];
}
export interface ResourceSpec { generation: string; model: string; mode: 'partial' | 'whole'; machine_count: number; cards_per_node: number; min_memory_gib: number; require_interconnect: boolean; queue: boolean; wait_minutes: number; note?: string }
export interface Assignment { device_id?: ID; node_id?: ID; host: string; slot?: number | string; slots?: string[]; node_name?: string; result?: {exit_code?: number}; status?: string }
export interface ResourceRequest { id: ID; owner_name: string; purpose: string; status: string; reason?: string; spec: ResourceSpec; created_at: string; protected_until?: string; devices: Assignment[] }
export interface Task { id: ID; name: string; owner_name: string; status: string; reason?: string; request_id?: ID; created_at: string; started_at?: string; finished_at?: string; ended_at?: string; exit_code?: number; spec: { resource?: ResourceSpec; timeout_seconds?: number }; nodes: Assignment[] }
export interface Sample { sampled_at: string; ai_core: number | null; memory_used: number | null; quality: string; sample_interval_seconds?: number; extensions?: Record<string, number | null> }
export interface MetricDescriptor { key: string; label?: string; name?: string; unit: string; scope?: string; display?: string; description?: string }
export interface Credentials { host: string; port: number; ssh_user: string; password: string }
export interface TaskLogs { nodes: { node_id: ID; host: string; text: string; status: string }[] }
