"""Read saved node errors; optionally probe SSH using Hive's real credentials/transport."""
import argparse
import json
from pathlib import Path

from hive.config import Settings
from hive.db import Database
from hive.domain import DomainError
from hive.inventory import Inventory, device_status
from hive.ssh import SSHTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--host', help='Only show/probe this registered host')
    parser.add_argument('--probe', action='store_true', help='Run a read-only Bash check over Hive SSH')
    args = parser.parse_args()
    config = Path(args.env_file).resolve()
    if not config.is_file():
        parser.error('Environment file does not exist: ' + str(config))
    if args.probe and not args.host:
        parser.error('--probe requires --host')
    settings = Settings.from_env(config)
    db = Database(settings)
    sql = 'SELECT id,host,port,ssh_user,status,reason,sampled_at FROM nodes WHERE deleted_at IS NULL'
    nodes = db.all(sql + (' AND host=%s' if args.host else ''), (args.host,) if args.host else ())
    for node in nodes:
        cards = db.all("""SELECT d.*,n.adapter,n.maintenance,o.request_id FROM devices d
                       JOIN nodes n ON n.id=d.node_id LEFT JOIN device_ownership o ON o.device_id=d.id
                       WHERE d.node_id=%s ORDER BY d.slot""", (node['id'],))
        node['devices'] = [dict(slot=d['slot'], status=device_status(d, stale_seconds=settings.stale_seconds),
                                quality=d['quality'], health=d['health'], ai_core=d['ai_core'],
                                memory_used=d['memory_used'], process_complete=bool(d['process_complete']),
                                sampled_at=d['sampled_at'], allocated=bool(d.get('request_id')))
                           for d in cards]
        node['available_cards'] = sum(d['status'] == 'available' for d in node['devices'])
        node['whole_node_available'] = bool(cards) and node['available_cards'] == len(cards)
    print(json.dumps({'env_file': str(config), 'known_hosts': str(settings.known_hosts.resolve()),
                      'known_hosts_exists': settings.known_hosts.is_file(), 'nodes': nodes},
                     ensure_ascii=False, default=str, indent=2))
    if args.host and not nodes:
        print('No registered node matches --host.')
        return 1
    if not args.probe:
        return 0
    transport = SSHTransport(settings)
    inventory = Inventory(db, settings)
    failed = False
    try:
        for row in nodes:
            try:
                node = inventory.connection(row['id'])
                result = transport.run(node, "printf 'HIVE_SSH_OK\\n'\n", timeout=10)
                ok = result.code == 0 and 'HIVE_SSH_OK' in result.stdout.splitlines()
                print(json.dumps({'host': row['host'], 'ssh_and_bash_ok': ok,
                                  'exit_code': result.code}, ensure_ascii=False))
                failed |= not ok
            except Exception as exc:
                # Do not print credentials, raw server output, or library exception strings.
                print(json.dumps({'host': row['host'], 'error_type': type(exc).__name__,
                                  'reason': str(exc) if isinstance(exc, DomainError)
                                  else 'Local error; check configuration and credential key.'}, ensure_ascii=False))
                failed = True
    finally:
        transport.close()
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
