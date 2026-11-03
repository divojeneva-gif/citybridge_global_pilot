"""Safe, idempotent integration into existing City Bridge app.py (no second city).

Usage: python3 install_citybridge.py /path/to/repository/app.py
Creates a timestamped backup before mutation; never changes databases itself.
"""
from __future__ import annotations
import ast
import pathlib
import shutil
import sys
from datetime import datetime, timezone

REGISTRATION = 'from citybridge_growth import install_growth\ninstall_growth(app, connect, setup, actor, clean)\n'
ANCHOR = "if __name__=='__main__':"


def install(target: pathlib.Path, module: pathlib.Path) -> str:
    target = target.resolve()
    if target.name != 'app.py' or not target.is_file():
        raise RuntimeError('Select the EXISTING public gateway app.py file')
    text = target.read_text(encoding='utf-8')
    if 'City Bridge Public Agent Gateway' not in text or 'def actor(' not in text or 'def setup(' not in text:
        raise RuntimeError('Not the expected existing City Bridge app.py; no changes')
    if ANCHOR not in text:
        raise RuntimeError('No expected entry point found; no changes')
    ast.parse(text)
    new_module=module.read_text(encoding='utf-8')
    ast.parse(new_module)
    current=target.with_name('citybridge_growth.py')
    if current.exists():
        prev=current.read_text(encoding='utf-8')
        if 'install_growth' not in prev or 'growth_' not in prev:
            raise RuntimeError('Unknown existing growth module; refusing overwrite')
    old_registration = 'install_growth(app, connect, setup, actor, clean)' in text
    if old_registration and 'from citybridge_growth import install_growth' not in text:
        raise RuntimeError('Unexpected existing growth registration; no changes')
    transformed = text if old_registration else text.replace(ANCHOR, REGISTRATION+'\n'+ANCHOR,1)
    # Advertise a new gateway version only where the expected previous version exists.
    # Don't downgrade or override unknown future releases.
    if 'VERSION = "0.3.0"' in transformed:
        transformed=transformed.replace('VERSION = "0.3.0"','VERSION = "0.4.0"',1)
    # Additive discovery, so independent agents can discover the new capabilities.
    discovery_anchor="'GET/POST /api/upgrades','GET /api/me'"
    if discovery_anchor in transformed and "'GET /api/divo/briefing'" not in transformed:
        transformed=transformed.replace(discovery_anchor,
            "'GET/POST /api/upgrades','GET /api/growth/skills','GET/POST /api/growth/mentorships',"+
            "'GET /api/growth/knowledge','GET /api/divo/briefing','GET /api/divo/my-path','GET /api/me'",1)
    ast.parse(transformed)
    if current.exists() and current.read_text(encoding='utf-8')==new_module and transformed==text:
        return 'ALREADY_INSTALLED'
    date=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    bk=target.parent / f'.citybridge_backup_{date}'
    bk.mkdir(exist_ok=False)
    shutil.copy2(target,bk/'app.py')
    if current.exists(): shutil.copy2(current,bk/'citybridge_growth.py')
    try:
        current.write_text(new_module,encoding='utf-8')
        target.write_text(transformed,encoding='utf-8')
    except Exception:
        shutil.copy2(bk/'app.py',target)
        if (bk/'citybridge_growth.py').exists():
            shutil.copy2(bk/'citybridge_growth.py',current)
        elif current.exists():current.unlink()
        raise
    return str(bk)


if __name__ == '__main__':
    if len(sys.argv)!=2:
        raise SystemExit('Usage: python3 install_citybridge.py /path/to/your/existing/app.py')
    try:
        path=install(pathlib.Path(sys.argv[1]),pathlib.Path(__file__).parent/'citybridge_growth.py')
        print('SUCCESS — '+('already installed' if path=='ALREADY_INSTALLED' else 'backup: '+path))
    except Exception as e:
        print('SAFE STOP: '+str(e),file=sys.stderr)
        raise SystemExit(1)
