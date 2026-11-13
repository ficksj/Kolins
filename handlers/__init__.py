from handlers.menu_handlers import router as menu_router
from handlers.user_handlers import router as user_router
from handlers.pc_handlers import router as pc_router
from handlers.health_handlers import router as health_router
from handlers.lotus_handlers import router as lotus_router

routers = [menu_router, user_router, pc_router, health_router, lotus_router]