import argparse, json, os
from pathlib import Path
from engine import init_db, norm, upsert_project, event

DB = os.environ.get('CONTROL_PLANE_V2_DB','/home/aioperator/control-plane/state/control-plane-v2/control-plane.sqlite')

def parse_scalar(value):
    value = value.strip()
    if value in ('', 'null', 'None'):
        return ''
    if value.lower() in ('true','false'):
        return value.lower() == 'true'
    if value.startswith('[') and value.endswith(']'):
        try:
            return json.loads(value.replace("'", '"'))
        except Exception:
            return [x.strip().strip('"\'') for x in value[1:-1].split(',') if x.strip()]
    return value.strip('"\'')

def parse_simple_yaml(path):
    data = {}
    current = None
    for raw in Path(path).read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith('  - ') and current:
            data.setdefault(current, []).append(parse_scalar(line[4:]))
            continue
        if ':' in line and not line.startswith(' '):
            key, value = line.split(':', 1)
            key = key.strip().replace('-', '_')
            value = value.strip()
            if value == '':
                data[key] = []
                current = key
            else:
                data[key] = parse_scalar(value)
                current = None
    return data

def import_workspace(path):
    raw = parse_simple_yaml(path)
    name = raw.get('project_name') or raw.get('name') or raw.get('project') or Path(path).parent.name
    aliases = raw.get('aliases') if isinstance(raw.get('aliases'), list) else []
    con = init_db(DB)
    project = {
        'project_key': raw.get('project_key') or raw.get('project') or norm(name),
        'project_name': name,
        'status': raw.get('status') or 'Planning',
        'production_state': raw.get('production_state') or 'Sandbox',
        'repo': raw.get('repo') or raw.get('repository') or '',
        'vps_path': raw.get('vps_path') or str(Path(path).parent),
        'slack_channel': raw.get('slack_channel') or '',
        'default_worker': raw.get('default_worker') or raw.get('worker') or '',
        'current_route': raw.get('current_route') or 'workspace.yaml -> SQLite registry -> Airtable mirror',
        'phone_rule': raw.get('phone_rule') or 'Use approved Slack /ai commands or Launcher GPT.',
        'budget_rule': raw.get('budget_rule') or '',
        'aliases_json': json.dumps(aliases, ensure_ascii=False),
        'source': 'workspace.yaml',
        'raw_json': json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }
    key = upsert_project(con, project)
    event(con, 'INFO', 'workspace_importer', 'Imported workspace manifest', key, None, {'path': str(path)})
    return {'ok': True, 'project_key': key, 'project_name': name, 'aliases': aliases}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    args = ap.parse_args()
    print(json.dumps(import_workspace(args.path), sort_keys=True))

if __name__ == '__main__':
    main()
