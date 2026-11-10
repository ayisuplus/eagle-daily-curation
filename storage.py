"""Persist selected groups through the existing Eagle API, verifying selected disk fields."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import os
import urllib.request
import sys
import tempfile
import time

from intake import STATE, canonical_source, read, save, sha, validate_group, restricted_ids, collection_fingerprint

SCRIPTS = Path.home()/'.codex/skills/eagle-story-assets/scripts'
sys.path.insert(0, str(SCRIPTS))
from eagle_common import api, all_items, check_library, request
from import_web_media import download


@contextmanager
def write_lock(state):
    # Same lock as legacy importers so Pi/Claude and this coordinator cannot overlap.
    from eagle_common import lock
    with lock('eagle-write'):
        yield


class EagleGateway:
    def check(self):
        check_library()

    def rows(self):
        return all_items()

    def get(self, iid):
        return api('item_get', {'ids': [iid], 'fullDetails': True})[0]

    def add(self, local, name, url, folder, tags):
        result = request('http://127.0.0.1:41595/api/item/addFromPath', {
            'path': str(local), 'name': name, 'website': url,
            'annotation': url, 'tags': tags, 'folderId': folder})
        if result.get('status') != 'success':
            raise RuntimeError('Eagle addFromPath did not confirm success; reconcile before retry')

    def link(self, item, url, folder, tags):
        urls = item.get('annotation', '').splitlines()
        if url not in urls:
            api('item_update', {'items': [{'id': item['id'], 'annotation': '\n\n'.join(
                s for s in (item.get('annotation', '').strip(), url) if s)}]})
        if folder not in item.get('folders', []):
            api('item_add_to_folders', {'ids': [item['id']], 'folders': [folder]})
        missing = [t for t in tags if t not in item.get('tags', [])]
        if missing:
            api('item_add_tags', {'ids': [item['id']], 'tags': missing})

    def disk(self, item):
        return read(Path(item['filePath']).parent/'metadata.json')


def verify_saved(gateway, iid, digest, url, folder, tags, before=None, attempts=5):
    """A successful API response is not proof that Eagle saved the change."""
    for n in range(attempts):
        gateway.check()
        item = gateway.get(iid)
        disk = gateway.disk(item)
        good = not item.get('isDeleted') and disk and not disk.get('isDeleted')
        if good:
            for view in (item, disk):
                good = good and url in view.get('annotation', '').splitlines()
                good = good and folder in view.get('folders', []) and set(tags) <= set(view.get('tags', []))
                if before:
                    good = good and all(view.get(k) == before.get(k) for k in ('name', 'url', 'star'))
                    good = good and set(before.get('tags', [])) <= set(view.get('tags', []))
                    good = good and set(before.get('folders', [])) <= set(view.get('folders', []))
                    good = good and before.get('annotation', '').strip() in view.get('annotation', '')
                else:
                    good = good and view.get('url') == url
            good = good and sha(item['filePath']) == digest
        if good:
            return {'itemId': iid, 'path': item['filePath'], 'sha256': digest,
                    'apiAndDiskVerified': True}
        if n + 1 < attempts:
            time.sleep(1)
    raise RuntimeError('Selected fields are not verified in both API and metadata.json; no automatic rewrite')


def prior_records(legacy):
    for section in ('items', 'results', 'imports'):
        for key, record in legacy.get(section, {}).items():
            if isinstance(record, dict) and record.get('sha256'):
                iid = record.get('itemId') or record.get('resultId') or (key if section == 'items' else None)
                if iid:
                    yield {'itemId': iid, 'sha256': record['sha256']}


def media_opener():
    # NO_PROXY alone suppresses urllib's Windows proxy discovery. Honor the
    # user's existing WinINET proxy for media, without changing any settings.
    proxies = urllib.request.getproxies()
    if os.name == 'nt' and not any(k in proxies for k in ('http', 'https')):
        proxies = urllib.request.getproxies_registry()
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies)).open


def import_group(group, state=STATE, legacy=None, gateway=None, reconcile=False):
    validate_group(group)
    if group['decision'] != 'collect':
        raise ValueError('Only selected collection groups may be saved')
    if not group.get('folderId'):
        raise ValueError('An observed existing Eagle folderId is required before import')
    state = Path(state)
    gateway = gateway or EagleGateway()
    gateway.check()
    key = canonical_source(group['pageUrl'])
    index_path = state/'imports.json'
    index = read(index_path, {'schemaVersion': 1, 'imports': {}})
    source_receipt = state/'groups'/(hashlib.sha256(key.encode()).hexdigest()+'.json')
    # Save the original post text and observations even when import stops half-way.
    old_group = read(source_receipt, {})
    if old_group:
        history_id = hashlib.sha256(json_bytes(old_group)).hexdigest()
        save(state/'group-history'/history_id, old_group)
    save(source_receipt, {**group, 'sourceKey': key, 'savedMedia': []})
    outcomes = []
    for media in group['media']:
        with tempfile.TemporaryDirectory(prefix='eagle-collection-') as temp:
            local = Path(media['localPath']) if media.get('localPath') else download({
                'mediaUrl': media['mediaUrl'], 'pageUrl': group['pageUrl']}, temp, media_opener())
            from PIL import Image
            with Image.open(local) as image:
                image.verify()
            digest = sha(local)
            records = index['imports']
            receipt_key = key + '/' + digest
            previous = records.get(receipt_key)
            if reconcile and not previous:
                raise RuntimeError('Reconciliation cannot import an unattempted page; no prior write intent exists')
            live = gateway.rows()
            live_ids = {r['id'] for r in live}
            all_records = [*records.values(), *prior_records(legacy or {})]
            if any(r.get('sha256') == digest and r.get('itemId') and r['itemId'] not in live_ids for r in all_records):
                outcome = {'order': media['order'], 'sha256': digest, 'status': 'previously-removed-not-restored'}
                outcomes.append(outcome)
                continue
            matches = [r for r in live if not r.get('isDeleted') and Path(r.get('filePath', '')).is_file()
                       and Path(r['filePath']).stat().st_size == local.stat().st_size and sha(r['filePath']) == digest]
            if previous and previous.get('status') in {'intent', 'blocked'} and not reconcile:
                raise RuntimeError('Uncertain prior write: use explicit read-only reconciliation, never auto retry')
            unresolved_elsewhere = [r for k, r in records.items() if k != receipt_key and
                                    r.get('sha256') == digest and r.get('status') in {'intent', 'blocked'}]
            if unresolved_elsewhere:
                raise RuntimeError('The same content has an uncertain write under another source; reconcile that original source first')
            before = None
            if matches:
                if reconcile or (previous and previous.get('status') in {'intent', 'blocked'}):
                    if previous.get('itemId'):
                        matches = [r for r in matches if r['id'] == previous['itemId']]
                    else:
                        matches = [r for r in matches if r['id'] not in previous.get('beforeIds', [])]
                    if len(matches) != 1:
                        raise RuntimeError('Uncertain import still has ambiguous media identities; manual recovery required')
                item = gateway.get(matches[0]['id'])
                if reconcile or (previous and previous.get('status') in {'intent', 'blocked'}):
                    before = previous.get('before') or (item if previous.get('status') == 'verified' else None)
                else:
                    before = item
                    already_linked = (group['pageUrl'] in item.get('annotation', '').splitlines()
                                      and group['folderId'] in item.get('folders', [])
                                      and set(group.get('tags', [])) <= set(item.get('tags', [])))
                    if item['id'] in restricted_ids(legacy or {}) and not already_linked:
                        raise RuntimeError('Legacy no-retry restriction applies to this exact item; preserve it for explicit repair')
                    records[receipt_key] = {'status': 'intent', 'sha256': digest, 'itemId': item['id'],
                                            'sourceKey': key, 'before': before}
                    save(index_path, index)
                    gateway.check()
                    current = gateway.get(item['id'])
                    if any(current.get(k) != item.get(k) for k in ('name', 'url', 'star', 'tags', 'folders', 'annotation')):
                        raise RuntimeError('Concurrent metadata change; reconcile before writing')
                    if not already_linked:
                        gateway.link(item, group['pageUrl'], group['folderId'], group.get('tags', []))
                action = 'reused'
            elif reconcile or (previous and previous.get('status') in {'intent', 'blocked'}):
                raise RuntimeError('No matching media found for uncertain write; manual recovery required')
            else:
                records[receipt_key] = {'status': 'intent', 'sha256': digest, 'sourceKey': key,
                                        'beforeIds': sorted(live_ids)}
                save(index_path, index)
                try:
                    gateway.check()
                    gateway.add(local, group['title']+f" · {media['order']:02}", group['pageUrl'],
                                group['folderId'], group.get('tags', []))
                    for attempt in range(5):
                        gateway.check()
                        current = gateway.rows()
                        matches = [r for r in current if r['id'] not in live_ids and
                                   Path(r.get('filePath', '')).is_file() and sha(r['filePath']) == digest]
                        if matches or attempt == 4:
                            break
                        # Eagle's plugin inventory can lag its native add response.
                        # Wait for readback; the add call is never repeated.
                        time.sleep(1)
                    if len(matches) != 1:
                        raise RuntimeError('Import returned without an unambiguous new media ID')
                    item = matches[0]
                    records[receipt_key]['itemId'] = item['id']
                    save(index_path, index)
                except Exception as exc:
                    records[receipt_key].update(status='blocked', reason=str(exc))
                    save(index_path, index)
                    raise
                action = 'imported'
            try:
                verified = verify_saved(gateway, item['id'], digest, group['pageUrl'],
                                        group['folderId'], group.get('tags', []), before)
            except Exception as exc:
                records[receipt_key].update(status='blocked', itemId=item['id'], reason=str(exc))
                save(index_path, index)
                raise
            records[receipt_key] = {**verified, 'sourceKey': key, 'status': 'verified'}
            save(index_path, index)
            outcomes.append({**verified, 'order': media['order'], 'status': action})
        save(source_receipt, {**group, 'sourceKey': key, 'savedMedia': outcomes})
    save(source_receipt, {**group, 'sourceKey': key, 'savedMedia': outcomes})
    return {'sourceKey': key, 'sidecar': str(source_receipt), 'media': outcomes,
            'collectionFingerprint': collection_fingerprint(group),
            'status': 'verified' if outcomes and all(o.get('apiAndDiskVerified') for o in outcomes) else 'not-collected'}


def json_bytes(value):
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')
