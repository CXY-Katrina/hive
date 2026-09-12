import type { Assignment } from '../types';
import { CredentialsButton } from './CredentialsModal';
import { Icon } from './ui';

export function Assignments({ devices, showCredentials = false }: { devices: Assignment[]; showCredentials?: boolean }) {
  const groups = new Map<string, { host: string; nodeId?: string | number; slots: string[] }>();
  devices.forEach(device => { const key = String(device.node_id ?? device.host); const group = groups.get(key) || { host: device.host, nodeId: device.node_id, slots: [] }; if (device.slot != null) group.slots.push(String(device.slot)); if (device.slots) group.slots.push(...device.slots); groups.set(key, group); });
  if (!groups.size) return null;
  return <div className="assignments">{Array.from(groups.entries()).map(([key, group]) => <div className="assignment" key={key}><Icon name="server" size={17} /><code>{group.host}</code><span>卡 {group.slots.length ? group.slots.join(', ') : '待确认'}</span>{showCredentials && group.nodeId != null && <CredentialsButton nodeId={group.nodeId} host={group.host} />}</div>)}</div>;
}
