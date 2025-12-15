from .add_booking import router as add_booking_router
from .add_client import router as add_client_router
from .booking_review import router as booking_review_router
from .invite_client import router as invite_client_router
from .list_clients import router as list_clients_router
from .master_menu import router as master_menu_router
from .register import router as register_router
from .reschedule import router as reschedule_router
from .schedule import router as schedule_router

routers = [
    register_router,
    master_menu_router,
    schedule_router,
    reschedule_router,
    booking_review_router,
    invite_client_router,
    add_booking_router,
    add_client_router,
    list_clients_router,
]
