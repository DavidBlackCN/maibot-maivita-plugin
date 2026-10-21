"""Launch the unmodified real Runner after installing I0-only I/O guards."""
from pathlib import Path
import os
import runpy
import sys

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from integration_tests.guard import install_guard
from integration_tests.source import HOST,verify_source

verify_source()
work=Path(os.environ['MAIBOT_INSTANCE_DIR']).resolve()
port=int(os.environ['MAIBOT_IPC_ADDRESS'].rsplit(':',1)[1])
install_guard(work,HOST,root,ipc_port=port)
if '--reject-token' in sys.argv:
    import asyncio
    from src.plugin_runtime.runner.rpc_client import RPCClient
    async def reject():
        client=RPCClient(os.environ['MAIBOT_IPC_ADDRESS'],os.environ['MAIBOT_SESSION_TOKEN'])
        try:
            assert not await client.connect_and_handshake()
        finally:
            await client.disconnect()
    asyncio.run(reject())
else:
    runpy.run_module('src.plugin_runtime.runner.runner_main',run_name='__main__')
