from .admin import router as admin_router
from .billing import router as billing_router
from .client import routers as client_routers
from .master import routers as master_routers
from .paywall import router as paywall_router
from .start import router as start_router

routers = [
    admin_router,
    start_router,
    billing_router,
    paywall_router,
    *master_routers,
    *client_routers,
]
