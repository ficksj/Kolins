from handlers.menu_handlers import router as menu_router
from handlers.user_handlers import router as user_router
from handlers.pc_handlers import router as pc_router

routers = [menu_router, user_router, pc_router]