"""Build a photo-only replacement from the verified MOMO starter server format."""
import argparse
import base64
import csv
import gzip
import hashlib
import io
import json
import re
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile

APP_SHA = '45da71851c5797bd8c3a5be8e44908a0bd5ff0711305325c5c58a0be07153eb7'
ROUTE = "if(req.method==='GET' && path==='/app.js') return await serve(res,'./app.js','text/javascript; charset=utf-8');"


def pack(value):
    return base64.b64encode(gzip.compress(value, mtime=0)).decode()


def unpack(value):
    return gzip.decompress(base64.b64decode(value, validate=True))


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'Expected exactly one match for {old[:100]!r}')
    return source.replace(old, new, 1)


def update(source, archive):
    match = re.search(r'^const ASSETS = (\{[^\n]+\});$', source, re.M)
    if not match:
        raise ValueError('Embedded assets were not found')
    assets = json.loads(match[1])
    browser = unpack(assets['./app.js'])
    if hashlib.sha256(browser).hexdigest() != APP_SHA:
        raise ValueError('Embedded app.js does not match the verified live room')
    images = {}
    with ZipFile(archive) as photos:
        sheet = photos.read('100문제_사진_질문_정답.csv').decode('utf-8-sig')
        rows = list(csv.DictReader(io.StringIO(sheet)))
        if len(rows) != 100:
            raise ValueError('Expected the approved 100-image archive')
        for number, row in enumerate(rows, 1):
            file = f'{number:03}.webp'
            if row['사진파일'] != file:
                raise ValueError('The archive manifest is out of order')
            raw = photos.read('level1/' + file)
            if raw[:4] != b'RIFF' or raw[8:12] != b'WEBP':
                raise ValueError(f'Invalid WebP image: {file}')
            scene = row['사진대상']
            if scene in images:
                if raw != images[scene][1]:
                    raise ValueError(f'Ambiguous photos for {scene}')
                continue
            images[scene] = (file, raw)
            assets['./starter-original-photos/' + file] = pack(raw)

    app = browser.decode()
    start = app.index('function photoUrl(')
    stop = app.index('\nfunction preloadNext', start)
    files = json.dumps({scene: photo[0] for scene, photo in images.items()}, ensure_ascii=False)
    app = app[:start] + (
        'const originalPhotoFiles = new Map(Object.entries(' + files + '));\n'
        'function photoUrl(scene){const file=originalPhotoFiles.get(scene);'
        "return file ? '/starter-original-photos/'+file : null;}"
    ) + app[stop:]
    app = replace_once(app, '  const original=PHOTO_IDS[currentScene]?PHOTO_BASE+PHOTO_IDS[currentScene]+PHOTO_PARAMS:null;\n', '')
    app = replace_once(app, '   let triedExternal=false;\n', '')
    app = replace_once(app, '    if(!triedExternal&&original){triedExternal=true;p.src=original;return;}\n', '')
    if app.count('credit.hidden=false') != 2:
        raise ValueError('Photo credit display has changed')
    app = app.replace('credit.hidden=false', 'credit.hidden=true')
    assets['./app.js'] = pack(app.encode())

    html = unpack(assets['./index.html']).decode()
    html = html.replace('사진 출처: Unsplash · ', '')
    assets['./index.html'] = pack(html.encode())
    source = replace_once(source, match[1], json.dumps(assets, separators=(',', ':')))
    route = ("if(req.method==='GET' && /^\\/starter-original-photos\\/\\d{3}\\.webp$/.test(path) "
             "&& Object.hasOwn(ASSETS,'.'+path)) return await serve(res,'.'+path,'image/webp');")
    return replace_once(source, ROUTE, route + '\n' + ROUTE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error('Output must be separate from the original server file')
    subprocess.run(['node', '--check', args.source], check=True, capture_output=True)
    result = update(args.source.read_text(), args.archive)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as folder:
        candidate = Path(folder) / 'server.mjs'
        candidate.write_text(result)
        subprocess.run(['node', '--check', candidate], check=True, capture_output=True)
        candidate.replace(args.output)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'MOMO photo restore stopped: {error}')
