"""Purpose-scoped collection decisions. No media implies a production obligation."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'state'
LEGACY = Path.home() / '.codex/automations/eagle/state'
ROLES = {'original', 'external-reference', 'mixed'}


def read(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else deepcopy(default)


def save(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for attempt in range(5):
        try:
            tmp.replace(p)
            break
        except PermissionError:
            if attempt == 4:
                raise
            # Windows indexers can briefly lock a newly-written JSON file.
            # Retry only the atomic local rename, never an Eagle API write.
            time.sleep(0.05 * (attempt + 1))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def collection_fingerprint(group):
    frozen = {k: v for k, v in group.items() if k != 'enrichment'}
    return hashlib.sha256(json.dumps(frozen, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def canonical_source(url):
    u = urlsplit(url)
    host = (u.hostname or '').lower()
    if u.scheme not in {'https', 'http'} or not host or u.username or u.password:
        raise ValueError('An observed public source URL is required')
    parts = [p for p in u.path.split('/') if p]
    if host == 'pinterest.com' or host.endswith('.pinterest.com'):
        if len(parts) == 2 and parts[0] == 'pin' and parts[1].isdigit():
            return 'pinterest:' + parts[1]
    if host in {'xiaohongshu.com', 'www.xiaohongshu.com'}:
        if len(parts) >= 2 and parts[0] in {'explore', 'discovery'}:
            return 'xiaohongshu:' + parts[-1]
    return urlunsplit((u.scheme, u.netloc.lower(), u.path.rstrip('/'), u.query, ''))


def validate_group(group):
    """Validate a human/model-observed judgement, never infer quality from format."""
    if not canonical_source(group['pageUrl']).startswith(('pinterest:', 'xiaohongshu:')):
        raise ValueError('Daily groups require an observed Pinterest or Xiaohongshu detail page')
    if not group.get('title', '').strip() or group.get('role') not in ROLES:
        raise ValueError('Title and source content role are required')
    if group.get('decision') not in {'collect', 'reject', 'hold'}:
        raise ValueError('Decision must be collect, reject or hold')
    evidence = group.get('evidence', {})
    if not evidence.get('viewedMedia') or not evidence.get('observation', '').strip():
        raise ValueError('Actual viewed media and a concrete observation are required')
    if group['decision'] == 'collect':
        if not group.get('selectionReason', '').strip() or not group.get('usableFor'):
            raise ValueError('Collection needs selection reason and purpose-scoped usability')
        media = group.get('media', [])
        if not media or any(not m.get('mediaUrl') and not m.get('localPath') for m in media):
            raise ValueError('Observed media or browser-obtained local files are required')
        orders = [m['order'] for m in media]
        if orders != list(range(1, len(media) + 1)):
            raise ValueError('Pages must be in observed order starting from 1')
        if not evidence.get('requiredPagesComplete'):
            raise ValueError('Incomplete context must remain on hold, not collected as complete')
        for use in group['usableFor']:
            if not use.get('purpose', '').strip() or not use.get('basis', '').strip():
                raise ValueError('Every usability judgement needs a purpose and visible basis')
    enrich = group.get('enrichment')
    if enrich:
        if group['decision'] != 'collect' or not all(enrich.get(k) for k in ('purpose', 'benefit', 'deliverable')):
            raise ValueError('Enrichment must have a selected purpose, benefit and deliverable')
        if enrich.get('status', 'selected') not in {'selected', 'blocked', 'completed', 'cancelled'}:
            raise ValueError('Invalid enrichment status')
        if enrich.get('status') == 'completed':
            if not enrich.get('conclusion') or not enrich.get('evidencePaths'):
                raise ValueError('Completed enrichment requires actual evidence and conclusion')
            for path in enrich['evidencePaths']:
                if not Path(path).is_file():
                    raise ValueError('Enrichment evidence is missing')
        if enrich.get('status') in {'blocked', 'cancelled'} and not enrich.get('reason'):
            raise ValueError('Blocked/cancelled enrichment requires a concrete reason')
        if enrich.get('status') == 'blocked' and not enrich.get('resumeCondition'):
            raise ValueError('Blocked enrichment requires a resume condition')
    return group


def restricted_ids(legacy):
    found = set()

    def visit(node, inherited=(), blocked=False):
        if isinstance(node, dict):
            ids = node.get('sourceIds') or node.get('itemIds') or inherited
            ids = [ids] if isinstance(ids, str) else ids
            if node.get('itemId') or node.get('sourceId'):
                ids = [node.get('itemId') or node.get('sourceId')]
            blocked = blocked or bool(node.get('stopAutomaticRetries') or node.get('doNotRepeatImportOrIncrementalWrite'))
            if blocked:
                found.update(ids)
            for child in node.values():
                visit(child, ids, blocked)
        elif isinstance(node, list):
            for child in node:
                visit(child, inherited, blocked)
    visit(legacy.get('nextActions', []))
    return found


def build_plan(groups):
    tasks = []
    keys = [canonical_source(g['pageUrl']) for g in groups]
    if len(keys) != len(set(keys)):
        raise ValueError('A work group must appear only once in a round')
    if sum(bool(g.get('enrichment')) for g in groups) > 1:
        raise ValueError('At most one enrichment group per daily round')
    if len([g for g in groups if g.get('decision') == 'collect']) > 5:
        raise ValueError('At most five work groups per daily round')
    for group in groups:
        validate_group(group)
        extra = group.get('enrichment')
        if extra and extra.get('status', 'selected') == 'selected':
            tasks.append({'sourceKey': canonical_source(group['pageUrl']), **extra})
    if len(tasks) > 1:
        raise ValueError('At most one enrichment group per daily round')
    return {'schemaVersion': 3, 'mode': 'collection-first', 'tasks': tasks,
            'collectionGroups': sum(g['decision'] == 'collect' for g in groups),
            'legacyQueuePolicy': 'archived-not-reassessed'}


def freeze_legacy(state=STATE, legacy=LEGACY):
    """Keep immutable snapshots, not a fabricated all-complete migration."""
    state, legacy = Path(state), Path(legacy)
    receipt_path = state / 'legacy-archive/receipt.json'
    if receipt_path.exists():
        receipt = read(receipt_path)
        for entry in receipt['files']:
            if sha(entry['snapshot']) != entry['sha256']:
                raise ValueError('Legacy archive was changed')
        return receipt
    files = []
    for name in ('dedup-index.json', 'progress-ledger-v2.json', 'web-import-index.json'):
        source = legacy / name
        if source.exists():
            data = source.read_bytes()
            target = state / 'legacy-archive' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != data:
                raise ValueError('Refusing to replace an existing historical snapshot')
            target.write_bytes(data)
            files.append({'source': str(source), 'snapshot': str(target.resolve()),
                          'sha256': hashlib.sha256(data).hexdigest()})
    receipt = {'policy': 'archived-not-reassessed', 'files': files,
               'createdAt': datetime.now().astimezone().isoformat(), 'legacyTasksScheduled': 0}
    save(receipt_path, receipt)
    return receipt


def seed_candidates(rows, legacy, receipts, feedback=None, limit=30):
    """Only explicit feedback/known originals seed taste; auto imports cannot vote."""
    generated = set(legacy.get('generatedItemIds', []))
    for result in legacy.get('results', {}).values():
        if isinstance(result, dict):
            generated.update(result[k] for k in ('itemId', 'resultId') if result.get(k))
    auto = {r['itemId'] for r in receipts.get('imports', {}).values() if r.get('itemId')}
    feedback = feedback or {}
    candidates = []
    for row in rows:
        iid = row['id']
        signal = feedback.get(iid)
        source = legacy.get('items', {}).get(iid, {})
        if iid in generated or iid in legacy.get('excludedItems', {}) or row.get('isDeleted'):
            continue
        if source.get('generated') or str(source.get('status', '')).startswith(('existing-result', 'derived-output')):
            continue
        if signal == 'dislike' or (iid in auto and signal not in {'like', 'adopted'}):
            continue
        if iid not in legacy.get('items', {}) and signal not in {'like', 'adopted'}:
            continue
        if row.get('ext', '').lower() not in {'jpg', 'jpeg', 'png', 'webp', 'avif', 'gif'}:
            continue
        priority = 2 if signal in {'like', 'adopted'} else 1
        candidates.append((priority, row))
    # Interleave folder groups without interpreting machine-generated stars as user taste.
    buckets = {}
    for _, row in sorted(candidates, key=lambda x: (-x[0], x[1]['id'])):
        bucket = (row.get('folders') or ['unfiled'])[0]
        buckets.setdefault(bucket, []).append(row)
    result = []
    while buckets and len(result) < limit:
        for bucket in list(buckets):
            result.append(buckets[bucket].pop(0))
            if not buckets[bucket]:
                del buckets[bucket]
            if len(result) == limit:
                break
    return result
