"""Agent-facing coordinator: browser observation is required before candidate manifests."""
import argparse
from datetime import datetime
import json
from uuid import uuid4
from pathlib import Path

from intake import ROOT, STATE, LEGACY, build_plan, canonical_source, freeze_legacy, read, save, seed_candidates, sha, collection_fingerprint


def archive_index(state):
    receipt = freeze_legacy(state)
    snapshots = {Path(f['source']).name: f['snapshot'] for f in receipt['files']}
    legacy = read(snapshots.get('dedup-index.json', ''), {}) if snapshots.get('dedup-index.json') else {}
    web = read(snapshots['web-import-index.json'], {}) if snapshots.get('web-import-index.json') else {}
    return legacy, web


def activation_status(state=STATE):
    rounds = []
    for path in sorted((Path(state)/'runs').glob('*/receipt.json')):
        receipt = read(path)
        rounds.append(receipt)
    latest = sorted(rounds, key=lambda r: r['createdAt'])[-3:]
    platforms = {p for r in latest for p, s in r['sources'].items() if s.get('status') == 'readable'}
    valid = [r for r in latest if r.get('liveValidated') and
             all(Path(m['path']).is_file() and sha(m['path']) == m['sha256']
                 for group in r['savedGroups'] for m in group['media'])]
    executions = [e for r in valid for e in r.get('executionIds', [])]
    saved_platforms = {g['sourceKey'].split(':', 1)[0] for r in valid for g in r['savedGroups']}
    ready = (len(valid) == 3 and len(executions) == len(set(executions)) and
             len(executions) >= 3 and {'pinterest', 'xiaohongshu'} <= platforms and
             {'pinterest', 'xiaohongshu'} <= saved_platforms)
    return {'ready': ready, 'successfulRounds': len(valid), 'requiredRounds': 3,
            'validatedPlatforms': sorted(platforms),
            'savedPlatforms': sorted(saved_platforms),
            'reason': 'ready' if ready else 'Need three actual saved rounds and observed access to both platforms'}


def finish_round(manifest, receipts, run_id, state=STATE):
    if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in run_id):
        raise ValueError('Run ID must be a simple unique directory name')
    plan = build_plan(manifest.get('groups', []))
    sources = manifest.get('sources', {})
    if set(sources) != {'pinterest', 'xiaohongshu'}:
        raise ValueError('Record the actual status of both requested platforms')
    for source in sources.values():
        if source.get('status') not in {'readable', 'blocked'} or not source.get('evidence'):
            raise ValueError('Each platform needs observed access evidence or a concrete blocker')
    expected = {canonical_source(g['pageUrl']) for g in manifest.get('groups', []) if g['decision'] == 'collect'}
    by_source = {r['sourceKey']: r for r in (receipts or [])}
    execution_ids = {r['executionId'] for r in (receipts or []) if r.get('executionId')}
    for path in (Path(state)/'runs').glob('*/receipt.json'):
        if execution_ids & set(read(path).get('executionIds', [])):
            raise ValueError('Execution evidence already belongs to a previous round')
    for execution_id in execution_ids:
        execution = read(Path(state)/'executions'/(execution_id+'.json'))
        claimed = [r for r in receipts if r.get('executionId') == execution_id]
        if not execution or execution.get('results') != claimed or execution.get('status') != 'finished':
            raise ValueError('Saved receipts are not bound to a finished import execution')
    saved = []
    blocked = []
    groups = {canonical_source(g['pageUrl']): g for g in manifest.get('groups', []) if g['decision'] == 'collect'}
    for key in expected:
        r = by_source.get(key)
        if not r or r.get('status') != 'verified' or not r.get('media'):
            blocked.append(r or {'sourceKey': key, 'status': 'blocked', 'reason': 'No saved receipt'})
            continue
        if not r.get('executionId') or r['executionId'] not in execution_ids:
            raise ValueError('Every saved selected group must belong to a verified import execution')
        if r.get('collectionFingerprint') != collection_fingerprint(groups[key]):
            raise ValueError('Receipt belongs to a different page sequence or collection decision')
        sidecar = read(r['sidecar'])
        clean_sidecar = {k: v for k, v in sidecar.items() if k not in {'sourceKey', 'savedMedia'}}
        if (canonical_source(sidecar['pageUrl']) != key or sidecar['savedMedia'] != r['media'] or
                collection_fingerprint(clean_sidecar) != r['collectionFingerprint']):
            raise ValueError('Group receipt is not backed by the actual sidecar')
        for media in r['media']:
            if not media.get('apiAndDiskVerified') or sha(media['path']) != media['sha256']:
                raise ValueError('Missing or changed saved media')
        saved.append(r)
    enrichment = [g['enrichment'] for g in manifest.get('groups', []) if g.get('enrichment')]
    unfinished = [t for t in enrichment if t.get('status', 'selected') in {'selected', 'blocked'}]
    for outcome in enrichment:
        if outcome.get('status') == 'completed':
            outcome['verifiedEvidence'] = [{'path': p, 'sha256': sha(p)} for p in outcome['evidencePaths']]
    folder = Path(state)/'runs'/run_id
    if (folder/'receipt.json').exists():
        raise ValueError('Use a new run ID; completed run receipts are immutable')
    receipt = {'runId': run_id, 'createdAt': datetime.now().astimezone().isoformat(),
               'sources': sources, 'savedGroups': saved, 'collectionGroups': len(saved),
               'enrichment': enrichment, 'enrichmentPending': unfinished, 'blockedGroups': blocked,
               'executionIds': sorted(execution_ids), 'legacyQueueScheduled': 0,
               'liveValidated': bool(saved) and bool(execution_ids) and not blocked and not unfinished and
                    any(s['status'] == 'readable' for s in sources.values())}
    save(folder/'manifest.json', manifest)
    save(folder/'receipt.json', receipt)
    lines = [f'# Eagle 素材精选 · {run_id}', '']
    for g in manifest.get('groups', []):
        if g['decision'] != 'collect':
            continue
        r = by_source.get(canonical_source(g['pageUrl']), {'status': 'blocked', 'reason': 'No saved receipt'})
        if r.get('status') != 'verified':
            lines.extend([f"{g['title']}：保存受阻，{r.get('reason', r.get('status'))}", ''])
            continue
        lines.extend([f"## {g['title']}", '', g['selectionReason'], '',
                      '用途：'+'、'.join(u['purpose'] for u in g['usableFor']), '',
                      f"[来源]({g['pageUrl']})", ''])
        for m in r['media']:
            lines.extend([f"![{g['title']}]({Path(m['path']).as_posix()})", '',
                          f"[在 Eagle 中打开](eagle://item/{m['itemId']})", ''])
    for platform, source in sources.items():
        if source['status'] == 'blocked':
            lines.extend([f"{platform}：{source['evidence']}", ''])
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'preview.md').write_text('\n'.join(lines), encoding='utf-8')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=STATE)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    sub.add_parser('activation')
    p = sub.add_parser('seeds'); p.add_argument('--out', required=True)
    p = sub.add_parser('plan'); p.add_argument('--manifest', required=True); p.add_argument('--out', required=True)
    p = sub.add_parser('import'); p.add_argument('--manifest', required=True)
    p.add_argument('--apply', action='store_true'); p.add_argument('--reconcile', action='store_true')
    p.add_argument('--out', required=True)
    p = sub.add_parser('finish'); p.add_argument('--manifest', required=True)
    p.add_argument('--receipts', required=True); p.add_argument('--run-id', required=True)
    p = sub.add_parser('feedback'); p.add_argument('--item-id', required=True)
    p.add_argument('--signal', choices=['like', 'dislike', 'adopted'], required=True)
    args = parser.parse_args(argv)
    state = args.state_dir
    if args.command == 'activation':
        result = activation_status(state)
    elif args.command == 'feedback':
        feedback = read(state/'feedback.json', {})
        feedback[args.item_id] = args.signal
        save(state/'feedback.json', feedback)
        result = {'itemId': args.item_id, 'signal': args.signal}
    elif args.command == 'init':
        result = freeze_legacy(state)
    elif args.command == 'seeds':
        from storage import EagleGateway
        gateway = EagleGateway(); gateway.check()
        legacy, _ = archive_index(state)
        rows = gateway.rows()
        imports = read(state/'imports.json', {'imports': {}})
        # Keep full metadata local. The small candidate file is for visual review.
        save(state/'library-snapshot.json', rows)
        selected = seed_candidates(rows, legacy, imports, read(state/'feedback.json', {}))
        save(args.out, selected)
        result = {'count': len(selected), 'path': args.out, 'status': 'candidates-need-visual-review'}
    else:
        manifest = read(args.manifest)
        plan = build_plan(manifest.get('groups', []))
        if args.command == 'plan':
            save(args.out, plan)
            result = plan
        elif args.command == 'finish':
            result = finish_round(manifest, read(args.receipts), args.run_id, state)
        elif not args.apply:
            save(args.out, {'preview': manifest, 'plan': plan})
            result = {'previewOnly': True, 'groups': plan['collectionGroups'], 'out': args.out}
        else:
            from storage import EagleGateway, import_group, write_lock
            legacy, web = archive_index(state)
            legacy['imports'] = web.get('imports', {})
            results = []
            execution_id = uuid4().hex
            execution_path = state/'executions'/(execution_id+'.json')
            execution = {'executionId': execution_id, 'status': 'in-progress', 'results': results}
            save(execution_path, execution)
            save(args.out, results)
            with write_lock(state):
                for group in manifest.get('groups', []):
                    if group['decision'] == 'collect':
                        try:
                            outcome = import_group(group, state, legacy, EagleGateway(), args.reconcile)
                        except Exception as exc:
                            outcome = {'sourceKey': canonical_source(group['pageUrl']), 'status': 'blocked',
                                       'reason': str(exc), 'collectionFingerprint': collection_fingerprint(group)}
                        results.append({**outcome, 'executionId': execution_id})
                        save(args.out, results)
                        save(execution_path, execution)
            execution['status'] = 'finished'
            save(execution_path, execution)
            result = results
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
