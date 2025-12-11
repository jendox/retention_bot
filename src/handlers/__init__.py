from .client import routers as client_routers
from .master import routers as master_routers
from .start import router as start_router

routers = [
    start_router,
    *master_routers,
    *client_routers,
]
