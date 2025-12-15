from .booking import router as booking_router
from .client_menu import router as client_menu_router
from .list_bookings import router as list_bookings_router
from .list_masters import router as list_masters_router
from .register import router as register_router
from .settings import router as settings_router

routers = [
    register_router,
    client_menu_router,
    booking_router,
    settings_router,
    list_bookings_router,
    list_masters_router,
]
