"""Select a fixed I1 target without changing the original I0 fixture."""
import os

BASELINES = {
    '1.3.12': ('5c7637e6913c84d1c3d5e742fa9e95a7e70d3501', '2.15.0'),
    '1.3.15': ('062d45cf8bd4272ac57d7ec03fcf405e5a458211', '2.16.0'),
}
HOST_VERSION = os.environ.get('MAIVITA_HOST_BASELINE', '1.3.12')
HOST_COMMIT, SDK_VERSION = BASELINES[HOST_VERSION]
