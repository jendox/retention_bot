from .admin import router as admin_router
from .client import routers as client_routers
from .master import routers as master_routers
from .start import router as start_router

routers = [
    admin_router,
    start_router,
    *master_routers,
    *client_routers,
]
