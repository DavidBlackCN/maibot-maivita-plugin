"""Offline upgrade of COPIES. Original stopped data is retained for matched rollback."""
from contextlib import ExitStack,closing
from pathlib import Path
import argparse,hashlib,json,shutil,sqlite3,sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'ecosystem/coordinator'))
sys.path.insert(0,str(ROOT/'sources/maibot-maivita-plugin'));sys.path.insert(0,str(ROOT/'sources/maibot-maicoordinate-plugin'))


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def upgrade(vita_data,coordinate_data,output,*,stopped=False):
    if not stopped:raise ValueError('Stop the Bot, then pass --stopped')
    vita_data=vita_data.resolve();coordinate_data=coordinate_data.resolve();output=output.resolve()
    if output.exists() or output.is_relative_to(vita_data) or output.is_relative_to(coordinate_data):
        raise ValueError('Choose a new output outside source data')
    from maivita.life.storage.safety import WriterLease
    from maivita.life.storage.schema import migrate
    from maivita.life.domain.clock import SystemClock
    from maicoordinate.config import Settings
    from maicoordinate.store import Store
    authority=vita_data/'engine-authority.json'
    lock_path=Path(json.loads(authority.read_text('utf-8'))['writer_lock']) if authority.exists() else vita_data/'writer.lock'
    excluded={lock_path.resolve(),(coordinate_data/'coordinate.lock').resolve()}
    def ignored(directory,names):
        return [n for n in names if (Path(directory)/n).resolve() in excluded or Path(n).suffix=='.lock']
    with ExitStack() as stack:
        stack.enter_context(WriterLease(lock_path))
        # Coordinate's lock protocol is identical to WriterLease on both platforms.
        stack.enter_context(WriterLease(coordinate_data/'coordinate.lock'))
        sources={'vita':vita_data,'coordinate':coordinate_data}
        original={}
        for label,source in sources.items():
            for p in source.rglob('*'):
                if p.is_symlink() or p.is_junction():raise ValueError('Linked data path refused')
            original[label]={p.relative_to(source).as_posix():digest(p) for p in source.rglob('*') if p.is_file() and p.suffix!='.lock' and p.resolve() not in excluded}
            shutil.copytree(source,output/'original'/label,ignore=ignored)
            shutil.copytree(source,output/'upgraded'/label,ignore=ignored)
        db=output/'upgraded/vita/life/maivita.db'
        if not db.exists():db=output/'upgraded/vita/maivita.db'
        with closing(sqlite3.connect(db)) as conn:version=migrate(conn,SystemClock())
        coordinate=output/'upgraded/coordinate/coordinate.sqlite3'
        # Preserve the original route binding while applying Store's versioned migration.
        routes=[]
        if coordinate.exists():
            with closing(sqlite3.connect(coordinate)) as conn:
                row=conn.execute("SELECT value FROM meta WHERE key='binding'").fetchone()
                if row:routes=json.loads(json.loads(row[0]))
        store=Store(coordinate,Settings(routes=routes));store.close()
        for label,source in sources.items():
            if original[label]!={p.relative_to(source).as_posix():digest(p) for p in source.rglob('*') if p.is_file() and p.suffix!='.lock' and p.resolve() not in excluded}:
                raise ValueError('Original data changed during upgrade')
        # Original snapshots are the rollback data. Verify restoration into a new
        # rehearsal directory, never overwrite running paths.
        for label in sources:
            shutil.copytree(output/'original'/label,output/'rollback-rehearsal'/label)
            restored={p.relative_to(output/'rollback-rehearsal'/label).as_posix():digest(p)
                for p in (output/'rollback-rehearsal'/label).rglob('*') if p.is_file() and p.suffix!='.lock' and p.resolve() not in excluded}
            if restored!=original[label]:raise ValueError('Rollback copy mismatch')
        report={'vita_schema':version,'coordinate_schema':2,'source_unchanged':True,
            'rollback_copy_verified':True,'original_hashes':original,
            'restore_rule':'Restore matching code + config + original data while stopped; do not downgrade an upgraded database in place.'}
        (output/'upgrade.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--vita-data',type=Path,required=True)
    p.add_argument('--coordinate-data',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--stopped',action='store_true')
    a=p.parse_args();print(json.dumps(upgrade(a.vita_data,a.coordinate_data,a.output,stopped=a.stopped),indent=2))
