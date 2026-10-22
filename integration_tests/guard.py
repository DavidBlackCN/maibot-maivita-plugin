"""Accidental-I/O guard for trusted I0 fixtures, not a malicious-code sandbox."""
from pathlib import Path
import os
import socket
import sys
import sysconfig


def install_guard(work, source, repository, *, ipc_port=None, allow_loopback_bind=False):
    work,source,repository=map(lambda p:Path(p).resolve(),(work,source,repository))
    # The interpreter may live outside a clean exported checkout. Trust only
    # its installed dependency directories, not a repository-relative venv or
    # arbitrary caller-supplied read roots. Writes remain temp-only.
    dependencies={Path(sysconfig.get_path(name)).resolve() for name in ('purelib','platlib')}
    read_roots=(work,source,Path(sys.base_prefix).resolve(),*dependencies)
    socketpair_code=getattr(socket.socketpair,'__code__',None)
    def inside(path,root):
        return path==root or root in path.parents
    def path_for(value):
        if isinstance(value,(str,bytes,os.PathLike)):
            return Path(os.fsdecode(value)).resolve()
        return None  # integer descriptors are owned by subprocess/IPC plumbing
    def audit(event,args):
        # Windows asyncio uses the stdlib's loopback socketpair for its wakeup
        # pipe. Allow only that exact stdlib function, not arbitrary loopback
        # endpoints requested by a fixture/plugin.
        internal_pair=(event in ('socket.bind','socket.connect') and socketpair_code is not None
                       and sys._getframe(1).f_code is socketpair_code)
        if internal_pair: return
        if event=='open':
            path=path_for(args[0])
            if path is None: return
            mode=args[1] or ''
            flags=args[2] or 0
            writing=any(c in str(mode) for c in 'wax+') or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
            if writing and not inside(path,work): raise PermissionError('I0_WRITE_OUTSIDE_TEMP')
            if not writing and not any(inside(path,root) for root in read_roots):
                # MaiVita is imported as source only, never from its config/data.
                if not ((inside(path,repository) and path.suffix in ('.py','.pyc')) or
                        (path.parent==repository/'maivita/life/storage/migrations' and path.suffix=='.sql')):
                    raise PermissionError('I0_READ_OUTSIDE_SOURCE_OR_TEMP: '+str(path))
        elif event in ('os.remove','os.rmdir','os.mkdir','os.chmod','os.utime'):
            path=path_for(args[0])
            if path is not None and not inside(path,work): raise PermissionError('I0_MUTATION_OUTSIDE_TEMP')
        elif event in ('os.rename','os.replace','os.link','os.symlink'):
            for value in args[:2]:
                path=path_for(value)
                if path is not None and not inside(path,work): raise PermissionError('I0_MUTATION_OUTSIDE_TEMP')
        elif event=='socket.connect':
            address=args[1]
            if not (isinstance(address,tuple) and address[0]=='127.0.0.1' and address[1]==ipc_port):
                raise PermissionError('I0_NETWORK_OUTSIDE_OWN_IPC')
        elif event=='socket.bind':
            address=args[1]
            if not (allow_loopback_bind and isinstance(address,tuple) and address[0]=='127.0.0.1' and address[1]==0):
                raise PermissionError('I0_BIND_OUTSIDE_EPHEMERAL_LOOPBACK')
    sys.addaudithook(audit)


def child_environment(work,host,repository,**extra):
    # Do not inherit deployment tokens, model credentials, proxy config or
    # MAIBOT_* settings. SDK and Host versions cannot be forced compatible.
    env={key:os.environ[key] for key in ('SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','NUMBER_OF_PROCESSORS') if key in os.environ}
    env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',
        PYTHONPATH=os.pathsep.join((str(repository),str(host))),
        MAIBOT_INSTANCE_DIR=str(work),TEMP=str(work),TMP=str(work),USERPROFILE=str(work))
    env.update(extra)
    return env
