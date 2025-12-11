from .client_menu import router as client_menu_router
from .register import router as register_router

routers = [
    register_router,
    client_menu_router,
]