from .admin import router as admin_router
from .billing import router as billing_router
from .client import routers as client_routers
from .demo import router as demo_router
from .master import routers as master_routers
from .notification_close import router as notification_close_router
from .paywall import router as paywall_router
from .start import router as start_router
from .support import router as support_router

routers = [
    admin_router,
    start_router,
    support_router,
    billing_router,
    paywall_router,
    demo_router,
    notification_close_router,
    *master_routers,
    *client_routers,
]
