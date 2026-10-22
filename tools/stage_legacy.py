"""Explicit offline staging only. Never activates a plugin or production path."""
import argparse
import json
from pathlib import Path
import sys
import types

root=Path(__file__).resolve().parents[1]
package=types.ModuleType('maivita_staging'); package.__path__=[str(root)]
sys.modules.setdefault(package.__name__,package)
from maivita_staging.maivita.life.storage.life_database import LifeDatabase
from maivita_staging.maivita.life.storage.safety import WriterLease
from maivita_staging.maivita.life.storage.legacy_import import import_snapshot
from maivita_staging.maivita.life.life_state.engine import LifeStateEngine


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--target',type=Path,required=True)
    parser.add_argument('--source-id',required=True)
    parser.add_argument('--timezone',default='Asia/Shanghai')
    parser.add_argument('--development-staging',action='store_true',required=True)
    parser.add_argument('--writer-lock',type=Path,required=True)
    args=parser.parse_args()
    if not args.target.is_absolute() or not args.snapshot.is_absolute(): parser.error('absolute staging target and snapshot required')
    if args.target.resolve()==args.snapshot.resolve(): parser.error('source and target must differ')
    if not args.writer_lock.is_absolute(): parser.error('absolute shared deployment writer lock required')
    authority=args.target.parent.parent/'engine-authority.json'
    if authority.exists():
        recorded=json.loads(authority.read_text(encoding='utf-8'))
        if recorded.get('writer_lock')!=str(args.writer_lock.resolve()):
            parser.error('writer lock differs from target engine authority')
    lease=WriterLease(args.writer_lock); lease.acquire()
    try:
        db=LifeDatabase(args.target) if args.target.exists() else LifeDatabase.create(args.target)
        LifeStateEngine(db).initialize()
        report=import_snapshot(db,args.snapshot,source_database_id=args.source_id,timezone=args.timezone)
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally: lease.release()


if __name__=='__main__': main()
